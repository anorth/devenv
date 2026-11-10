"""devenv apply: the pure rules, and whole runs in a temporary home with stub tools."""

import hashlib
import json

import pytest

from devenv import apply
from devenv.apply import (
    BASHRC_BLOCK,
    OLD_BASHRC_BLOCK,
    ZSHENV,
    deep_merge,
    with_bashrc_block,
)


def test_deep_merge_keeps_unrelated_keys():
    base = {"model": "x", "attribution": {"commit": "Co-authored", "other": 1}, "list": [1]}
    add = {"attribution": {"commit": "", "pr": ""}}
    assert deep_merge(base, add) == {
        "model": "x",
        "attribution": {"commit": "", "other": 1, "pr": ""},
        "list": [1],
    }
    assert base["attribution"] == {"commit": "Co-authored", "other": 1}


def test_deep_merge_replaces_a_non_object():
    merged = deep_merge({"attribution": "on"}, {"attribution": {"pr": ""}})
    assert merged == {"attribution": {"pr": ""}}


def test_bashrc_block_is_appended_once():
    text = "alias ll='ls -l'\n"
    once = with_bashrc_block(text)
    assert once == text + "\n" + BASHRC_BLOCK
    assert with_bashrc_block(once) == once
    assert with_bashrc_block("") == BASHRC_BLOCK


def test_bashrc_block_is_replaced_in_place():
    stale = BASHRC_BLOCK.replace("common.sh", "old.sh")
    text = f"before\n{stale}after\n"
    assert with_bashrc_block(text) == f"before\n{BASHRC_BLOCK}after\n"


def test_old_unmarked_block_is_converted():
    text = f"before\n\n{OLD_BASHRC_BLOCK}after\n"
    converted = with_bashrc_block(text)
    assert converted == f"before\n\n{BASHRC_BLOCK}after\n"
    assert "# dev-base:" not in converted
    assert with_bashrc_block(converted) == converted


def test_unterminated_block_is_refused():
    with pytest.raises(ValueError):
        with_bashrc_block(f"{apply.BASHRC_START}\nexport PATH=x\n")


# ---------------------------------------------------------------- whole runs


@pytest.fixture
def home(machine):
    """A machine where every tool apply calls is a stub that logs its arguments."""
    log = machine.home.parent / "calls"
    for tool in ["mise", "npx", "claude", "cursor-agent"]:
        machine.stub(tool, f'echo "{tool} $*" >> "{log}"')
    dotfiles = machine.home / "dotfiles"
    dotfiles.mkdir()
    (dotfiles / "install.sh").write_text(f'#!/bin/sh\necho "install.sh" >> "{log}"\n')
    (dotfiles / "install.sh").chmod(0o755)
    machine.calls = log
    return machine


def digests(home):
    paths = [".zshenv", ".bashrc", ".zshrc", ".claude/settings.json", ".cursor/cli-config.json"]
    present = [p for p in paths if (home / p).exists()]
    return {p: hashlib.sha256((home / p).read_bytes()).hexdigest() for p in present}


def test_apply_runs_every_step_and_a_second_run_changes_nothing(home):
    (home.home / ".bashrc").write_text(f"# distro bashrc\n\n{OLD_BASHRC_BLOCK}")
    result = home.ok("apply")
    assert "apply finished with no warnings" in result.stdout

    calls = home.calls.read_text().splitlines()
    assert calls[0] == "install.sh"
    assert "mise use -g node@lts pnpm@latest uv@latest yq@latest" in calls
    assert "mise use -g --yes npm:beads-ui@latest" in calls
    assert "npx --yes playwright install chromium" in calls
    assert not any(c.startswith(("claude", "cursor-agent")) for c in calls)

    h = home.home
    assert (h / ".zshenv").read_text() == ZSHENV
    assert (h / ".bashrc").read_text() == f"# distro bashrc\n\n{BASHRC_BLOCK}"
    settings = json.loads((h / ".claude/settings.json").read_text())
    assert settings == {"attribution": {"commit": "", "pr": ""}}
    assert (h / ".zshrc").read_text() == apply.ZSHRC_FALLBACK

    before = digests(h)
    mtimes = {p: (h / p).stat().st_mtime_ns for p in before}
    again = home.ok("apply")
    assert digests(h) == before
    assert {p: (h / p).stat().st_mtime_ns for p in before} == mtimes
    assert "is up to date" in again.stdout


def test_settings_keep_other_keys(home):
    settings = home.home / ".claude/settings.json"
    settings.parent.mkdir()
    settings.write_text(json.dumps({"model": "opus", "attribution": {"commit": "x"}}))
    home.ok("apply")
    merged = json.loads(settings.read_text())
    assert merged == {"model": "opus", "attribution": {"commit": "", "pr": ""}}


def test_invalid_settings_are_left_untouched_and_fail_the_run(home):
    settings = home.home / ".cursor/cli-config.json"
    settings.parent.mkdir()
    settings.write_text("{not json")
    result = home.run("apply")
    assert result.returncode == 1
    assert settings.read_text() == "{not json"
    assert "required steps failed: agent settings" in result.stderr
    # The rest still ran.
    assert (home.home / ".zshenv").read_text() == ZSHENV
    assert json.loads((home.home / ".claude/settings.json").read_text())["attribution"]["pr"] == ""


def test_existing_zshrc_is_left_alone(home):
    (home.home / ".zshrc").write_text("mine\n")
    home.ok("apply")
    assert (home.home / ".zshrc").read_text() == "mine\n"


def test_zshrc_symlink_is_never_written_through(home):
    zshrc = home.home / ".zshrc"
    zshrc.symlink_to(home.home / "dotfiles/zshrc")  # dangling, as before dotfiles are cloned
    home.ok("apply")
    assert zshrc.is_symlink()
    assert not (home.home / "dotfiles/zshrc").exists()

    (home.home / "dotfiles/zshrc").write_text("real\n")
    home.ok("apply")
    assert (home.home / "dotfiles/zshrc").read_text() == "real\n"


def test_optional_failures_warn_and_required_ones_fail(home):
    home.stub("npx", "exit 1")
    result = home.ok("apply")
    assert "playwright install chromium failed (optional, continuing)" in result.stderr
    assert "apply finished with 1 warning(s)" in result.stdout

    home.stub("mise", "exit 1")
    result = home.run("apply")
    assert result.returncode == 1
    assert "required steps failed: mise tools" in result.stderr
    # Four mise agent tools and Chromium also warned.
    assert "apply finished with 6 warning(s)" in result.stdout


def test_a_missing_agent_cli_is_installed(home):
    (home.bin / "claude").unlink()
    # Only the stubs and the system, so a real claude can't be found.
    home.env["PATH"] = f"{home.bin}:/usr/bin:/bin"
    installer = f"echo installer ran >> {home.calls}"
    home.stub("curl", f'echo "curl $*" >> "{home.calls}"; echo "{installer}"')
    home.ok("apply")
    calls = home.calls.read_text().splitlines()
    assert "curl -fsSL https://claude.ai/install.sh" in calls
    assert "installer ran" in calls


def test_apply_refuses_root(monkeypatch):
    monkeypatch.setattr(apply.os, "geteuid", lambda: 0)
    with pytest.raises(apply.Die, match="not root"):
        apply.apply()


def test_upgrade_failure_doesnt_apply(home):
    home.stub("uv", "exit 1")
    result = home.run("upgrade")
    assert result.returncode == 1
    assert "nothing was applied" in result.stderr
    assert not home.calls.exists()


def test_upgrade_runs_apply_as_a_new_process(home):
    home.stub("uv", f'echo "uv $*" >> "{home.calls}"')
    home.stub("devenv", f'echo "devenv $*" >> "{home.calls}"; exit 3')
    result = home.run("upgrade")
    assert result.returncode == 3
    assert home.calls.read_text().splitlines() == ["uv tool upgrade devenv", "devenv apply"]


def test_a_symlinked_shell_file_is_read_but_never_written_through(home):
    target = home.home / "dotfiles/bashrc"
    target.write_text("mine\n")
    (home.home / ".bashrc").symlink_to(target)
    result = home.run("apply")
    assert result.returncode == 1
    assert "is a symlink, so it's left alone" in result.stderr
    assert target.read_text() == "mine\n"

    target.write_text(f"mine\n\n{BASHRC_BLOCK}")
    home.ok("apply")


def test_a_symlinked_settings_file_warns_with_what_to_add(home):
    target = home.home / "dotfiles/claude-settings.json"
    target.write_text('{"model": "opus"}\n')
    settings = home.home / ".claude/settings.json"
    settings.parent.mkdir()
    settings.symlink_to(target)
    result = home.ok("apply")
    assert f"{settings} is a symlink to {target}" in result.stderr
    assert '"commit": ""' in result.stderr
    assert "apply finished with 1 warning(s)" in result.stdout
    assert target.read_text() == '{"model": "opus"}\n'

    target.write_text('{"model": "opus", "attribution": {"commit": "", "pr": ""}}\n')
    result = home.ok("apply")
    assert "apply finished with no warnings" in result.stdout
