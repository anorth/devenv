"""Converging the user-level environment: tools, agent settings and shell files.

Each step is a plain function taking the home directory and a Run, so a later
subcommand can run one on its own. Nothing here removes anything.
"""

import json
import os
import shutil
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from devenv import rules
from devenv.system import call, capture, status
from devenv.ui import Die, log, warn

# ---------------------------------------------------------------- the data

DOTFILES_URL = "https://github.com/anorth/dotfiles"
DOTFILES_DIR = "dotfiles"

# Installed with one `mise use -g`. Required.
MISE_TOOLS = ["node@lts", "pnpm@latest", "uv@latest", "yq@latest"]


@dataclass(frozen=True)
class Installer:
    """A vendor's install script, piped from curl to the shell its docs name."""

    url: str
    shell: str = "bash"
    env: dict[str, str] = field(default_factory=dict)
    # Run with no controlling terminal, for an installer that prompts on
    # /dev/tty whenever it can open it and has no setting to stop that.
    detach: bool = False


# They update themselves, so they're installed only when missing. Optional.
AGENT_INSTALLERS = {
    "claude": Installer("https://claude.ai/install.sh"),
    "cursor-agent": Installer("https://cursor.com/install"),
    # Without this it asks on the terminal whether to start Codex.
    "codex": Installer(
        "https://chatgpt.com/codex/install.sh", shell="sh", env={"CODEX_NON_INTERACTIVE": "1"}
    ),
    # Grok Build. It links grok and agent into ~/.local/bin (taking agent from
    # Cursor's, which is fine). A $SHELL it doesn't know is the only way to stop
    # it adding a block to ~/.zshrc or ~/.bashrc, which it writes through a
    # symlink into the dotfiles.
    "grok": Installer("https://x.ai/cli/install.sh", env={"SHELL": "/bin/sh"}),
    # It asks what to do, and whether to start pi, on the terminal.
    "pi": Installer("https://pi.dev/install.sh", shell="sh", detach=True),
}

# Each is its own `mise use -g`, so one failing doesn't stop the others. Optional.
MISE_AGENT_TOOLS = [
    # bd from its GitHub release: the npm package fetches the binary in a
    # postinstall script, which mise's npm installs don't run.
    ["github:steveyegge/beads@latest"],
    # mise flags npm packages with few downloads; this one is chosen on purpose.
    ["--yes", "npm:beads-ui@latest"],
]

# Deep-merged into each file under home. No agent credits itself in commits or
# PRs. Codex has no local setting for this, and pi doesn't add any.
AGENT_SETTINGS = {
    ".claude/settings.json": {"attribution": {"commit": "", "pr": ""}},
    ".cursor/cli-config.json": {
        "attribution": {"attributeCommitsToAgent": False, "attributePRsToAgent": False}
    },
}

USER_PATH = "$HOME/bin:$HOME/.local/bin:$HOME/.local/share/mise/shims:$PATH"

ZSHENV = f'export PATH="{USER_PATH}"\n'

COMMON_SH = "[ -f ~/.config/shell/common.sh ] && . ~/.config/shell/common.sh"

BASHRC_START = "# >>> devenv: same PATH and shared shell config as zsh >>>"
BASHRC_END = "# <<< devenv <<<"
BASHRC_BLOCK = f'{BASHRC_START}\nexport PATH="{USER_PATH}"\n{COMMON_SH}\n{BASHRC_END}\n'

# What older dev-base images appended, unmarked. Replaced by the block once.
OLD_BASHRC_BLOCK = (
    "# dev-base: same PATH and shared shell config as zsh\n"
    f'export PATH="{USER_PATH}"\n'
    f"{COMMON_SH}\n"
)

ZSHRC_FALLBACK = """\
# Fallback from devenv apply; the real config comes from ~/dotfiles.
autoload -Uz compinit && compinit
command -v mise >/dev/null && eval "$(mise activate zsh)"
command -v direnv >/dev/null && eval "$(direnv hook zsh)"
[[ -f ~/.config/shell/common.sh ]] && source ~/.config/shell/common.sh
"""

# ---------------------------------------------------------------- a run


@dataclass
class Run:
    """Counts warnings, and names the required steps that failed."""

    warnings: int = 0
    failed: list[str] = field(default_factory=list)

    def warning(self, message: str) -> None:
        self.warnings += 1
        warn(message)

    def optional_failed(self, message: str) -> None:
        self.warnings += 1
        warn(f"{message} (optional, continuing)")

    def required_failed(self, step: str, message: str) -> None:
        self.warnings += 1
        self.failed.append(step)
        warn(message)


# ---------------------------------------------------------------- pure logic


def deep_merge(base: dict, add: dict) -> dict:
    """`add` merged into `base`, recursing into objects, as jq's `*` does."""
    merged = dict(base)
    for key, value in add.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def with_bashrc_block(text: str) -> str:
    """A .bashrc with devenv's block replaced in place, converted from the old one, or appended."""
    start = text.find(BASHRC_START)
    if start != -1:
        end = text.find(BASHRC_END, start)
        if end == -1:
            raise ValueError(f"it has the line {BASHRC_START!r} but not {BASHRC_END!r}")
        after = text.find("\n", end)
        after = len(text) if after == -1 else after + 1
        return text[:start] + BASHRC_BLOCK + text[after:]
    if OLD_BASHRC_BLOCK in text:
        return text.replace(OLD_BASHRC_BLOCK, BASHRC_BLOCK, 1)
    if not text:
        return BASHRC_BLOCK
    return text + ("\n" if text.endswith("\n") else "\n\n") + BASHRC_BLOCK


# ---------------------------------------------------------------- files


def _write_if_changed(path: Path, text: str) -> bool:
    """Write a file only if its content would change; say whether it did.

    Never writes through a symlink, which would edit the file it points to.
    """
    if path.is_file() and path.read_text() == text:
        return False
    if path.is_symlink():
        raise OSError(f"{path} is a symlink, so it's left alone")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return True


def _say_written(path: Path, changed: bool) -> None:
    log(f"Wrote {path}" if changed else f"{path} is up to date")


# ---------------------------------------------------------------- steps


def _mise_use(args: list[str]) -> bool:
    return call(["mise", "use", "-g", *args])


def _linked_dotfiles(home: Path) -> Path | None:
    """The dotfiles checkout that ~/.zshrc links into, wherever it is.

    That's how a machine with its dotfiles somewhere other than ~/dotfiles
    keeps them: cloning a second copy would relink ~/.zshrc and the rest away
    from the one being edited.
    """
    zshrc = home / ".zshrc"
    if not zshrc.is_symlink():
        return None
    target_dir = Path(os.path.realpath(zshrc)).parent
    if not target_dir.is_dir():
        return None
    top = capture(["git", "-C", str(target_dir), "rev-parse", "--show-toplevel"])
    if top.returncode != 0:
        return None
    checkout = Path(top.stdout.strip())
    origin = capture(["git", "-C", str(checkout), "remote", "get-url", "origin"])
    if origin.returncode != 0 or rules.repo_id(origin.stdout) != rules.repo_id(DOTFILES_URL):
        return None
    return checkout


def apply_dotfiles(home: Path, run: Run) -> None:
    """Clone the dotfiles if they're missing, then run their installer.

    They're ~/dotfiles, or else the checkout ~/.zshrc links into. An existing
    checkout is never pulled or reset: it's the user's to manage.
    """
    dotfiles = home / DOTFILES_DIR
    if not (dotfiles.exists() or dotfiles.is_symlink()) and (linked := _linked_dotfiles(home)):
        log(f"Using the dotfiles at {linked}, which ~/.zshrc links into")
        dotfiles = linked
    if not (dotfiles.exists() or dotfiles.is_symlink()):
        log(f"Cloning {DOTFILES_URL} to {dotfiles}")
        if not call(["git", "clone", DOTFILES_URL, str(dotfiles)]):
            run.required_failed(
                "dotfiles",
                f"couldn't clone {DOTFILES_URL}; check the network and run devenv apply again",
            )
            return
    log(f"Running {dotfiles}/install.sh")
    if not call([str(dotfiles / "install.sh")], cwd=home):
        run.required_failed("dotfiles", f"{dotfiles}/install.sh failed; see its output above")


def apply_mise_tools(home: Path, run: Run) -> None:
    """Install or upgrade the global tools. Re-running `@latest` upgrades them."""
    log(f"mise global tools: {' '.join(MISE_TOOLS)}")
    if not _mise_use(MISE_TOOLS):
        run.required_failed(
            "mise tools", "mise use -g failed for the global tools; see its output above"
        )


def _mise_shims(home: Path) -> Path:
    return home / ".local/share/mise/shims"


def _installed_outside_mise(home: Path, command: str) -> bool:
    """Whether a command is on PATH, not counting mise's shims.

    An agent CLI that devenv used to install through mise has a shim, but its
    own installer should still run.
    """
    found = shutil.which(command)
    return found is not None and Path(found).parent != _mise_shims(home)


def _installer_path(home: Path) -> str:
    """PATH for the installers: mise's shims left out, mise's node put in by its real directory.

    A shim left from devenv's old mise installs would otherwise look like a
    conflicting install (pi's installer refuses to replace one). Pi's needs node.
    """
    shims = str(_mise_shims(home))
    dirs = [d for d in os.environ.get("PATH", "").split(os.pathsep) if d.rstrip("/") != shims]
    node = capture(["mise", "which", "node"], cwd=home)
    if node.returncode == 0 and node.stdout.strip():
        dirs.insert(0, str(Path(node.stdout.strip()).parent))
    return os.pathsep.join(dirs)


def apply_agent_clis(home: Path, run: Run) -> None:
    """Install missing agent CLIs, and install or upgrade the ones from mise."""
    # Some installers link into ~/.local/bin only if it's already there.
    (home / ".local/bin").mkdir(parents=True, exist_ok=True)
    path = None
    for command, installer in AGENT_INSTALLERS.items():
        if _installed_outside_mise(home, command):
            log(f"{command} is installed (it updates itself)")
            continue
        log(f"Installing {command} from {installer.url}")
        path = path or _installer_path(home)
        env = {**os.environ, **installer.env, "PATH": path}
        # The one place a shell runs: vendor installers are piped from curl.
        script = f"curl -fsSL {installer.url} | {installer.shell}"
        cmd = ["bash", "-o", "pipefail", "-c", script]
        if not call(cmd, cwd=home, env=env, detach=installer.detach):
            run.optional_failed(f"couldn't install {command} from {installer.url}")
    for args in MISE_AGENT_TOOLS:
        log(f"mise use -g {' '.join(args)}")
        if not _mise_use(args):
            run.optional_failed(f"mise use -g {' '.join(args)} failed")


class SymlinkedSettings(Exception):
    """A settings file that needs changing is a symlink, probably into dotfiles."""


def merge_settings_file(path: Path, add: dict) -> bool:
    """Deep-merge `add` into a JSON file, keeping its other keys; say whether it changed.

    A file that isn't a JSON object is left untouched, since overwriting it
    would lose an agent's config. A symlink is never written through: the
    file it points to is someone else's to edit.
    """
    text = path.read_text() if path.is_file() else ""
    if text.strip():
        try:
            current = json.loads(text)
        except json.JSONDecodeError as err:
            raise ValueError(f"{path} isn't valid JSON ({err})") from None
        if not isinstance(current, dict):
            raise ValueError(f"{path} isn't a JSON object")
    else:
        current = {}
    merged = deep_merge(current, add)
    if merged == current and text.strip():
        return False
    if path.is_symlink():
        raise SymlinkedSettings(
            f"{path} is a symlink to {os.readlink(path)}, so devenv leaves it alone. "
            f"Merge this into it yourself:\n{json.dumps(add, indent=2)}"
        )
    return _write_if_changed(path, json.dumps(merged, indent=2) + "\n")


def apply_agent_settings(home: Path, run: Run) -> None:
    for rel, add in AGENT_SETTINGS.items():
        path = home / rel
        try:
            _say_written(path, merge_settings_file(path, add))
        except SymlinkedSettings as err:
            run.warning(str(err))
        except (ValueError, OSError) as err:
            run.required_failed(
                "agent settings", f"{err}; fix it by hand and run devenv apply again"
            )


def apply_chromium(home: Path, run: Run) -> None:
    """Playwright's Chromium for the user. Its system libraries come with the machine."""
    log("Playwright's Chromium (a download of about 300 MB when there's a new one)")
    # Its output is mostly a warning box meant for projects, so show it only on failure.
    result = capture(["npx", "--yes", "playwright", "install", "chromium"], cwd=home)
    if result.returncode != 0:
        print(result.stdout + result.stderr, file=sys.stderr, end="")
        run.optional_failed("npx playwright install chromium failed")


def apply_shell_files(home: Path, run: Run) -> None:
    """PATH for every shell, a marked .bashrc block, and a .zshrc only if there's none."""
    zshenv = home / ".zshenv"
    try:
        _say_written(zshenv, _write_if_changed(zshenv, ZSHENV))
    except OSError as err:
        run.required_failed("shell files", f"can't write {zshenv}: {err}")

    bashrc = home / ".bashrc"
    try:
        current = bashrc.read_text() if bashrc.is_file() else ""
        _say_written(bashrc, _write_if_changed(bashrc, with_bashrc_block(current)))
    except (ValueError, OSError) as err:
        run.required_failed("shell files", f"can't update {bashrc}: {err}; fix it by hand")

    zshrc = home / ".zshrc"
    # A dangling symlink doesn't exist but must still be left alone.
    if zshrc.exists() or zshrc.is_symlink():
        return
    log(f"No {zshrc}, so writing a minimal fallback (the dotfiles normally provide it)")
    try:
        _write_if_changed(zshrc, ZSHRC_FALLBACK)
    except OSError as err:
        run.required_failed("shell files", f"can't write {zshrc}: {err}")


STEPS: list[Callable[[Path, Run], None]] = [
    apply_dotfiles,
    apply_mise_tools,
    apply_agent_clis,
    apply_agent_settings,
    apply_chromium,
    apply_shell_files,
]


def apply(home: Path | None = None) -> None:
    """Run every step in order, then fail if a required one did."""
    if os.geteuid() == 0:
        raise Die("apply sets up the dev user's environment; run it as that user, not root")
    home = home or Path.home()
    # A new machine's shells may not have these yet: .zshenv is written last.
    user_bins = [str(home / ".local/bin"), str(home / ".local/share/mise/shims")]
    os.environ["PATH"] = os.pathsep.join([*user_bins, os.environ.get("PATH", "")])
    run = Run()
    for step in STEPS:
        step(home, run)
    print(flush=True)
    if run.warnings:
        log(f"apply finished with {run.warnings} warning(s); see the !!! lines above")
    else:
        log("apply finished with no warnings")
    if run.failed:
        raise Die(f"required steps failed: {', '.join(dict.fromkeys(run.failed))}")


def upgrade() -> int:
    """Upgrade devenv with uv, then apply with the new version in a new process."""
    log("uv tool upgrade devenv")
    if not call(["uv", "tool", "upgrade", "devenv"]):
        raise Die("uv tool upgrade devenv failed, so nothing was applied; see uv's output above")
    devenv = shutil.which("devenv")
    if not devenv:
        raise Die(
            "devenv isn't on PATH after the upgrade; "
            "run uv tool install git+https://github.com/anorth/devenv"
        )
    log(f"{devenv} apply")
    return status([devenv, "apply"])
