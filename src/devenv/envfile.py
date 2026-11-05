"""Reading and writing .env files, one of the contract's kinds of state."""

from pathlib import Path

from devenv import rules
from devenv.system import capture
from devenv.ui import warn

# Secrets and flags, in the main checkout and linked from each worktree.
SHARED_ENV = ".env"
# What's particular to one checkout or worktree. It overrides the shared file.
LOCAL_ENV = ".env.local"


def read_env(path: Path) -> str:
    """A .env file's text; a missing one reads as empty."""
    try:
        return path.read_text(encoding="utf-8", errors="surrogateescape")
    except FileNotFoundError:
        return ""


def env_file_get(path: Path, key: str) -> str:
    return rules.env_get(read_env(path), key)


def checkout_env_get(path: Path, key: str) -> str:
    """KEY's value for a checkout or worktree, resolved as its dev server would."""
    return env_file_get(path / LOCAL_ENV, key) or env_file_get(path / SHARED_ENV, key)


def env_file_set(path: Path, key: str, value: str) -> None:
    text = rules.env_set(read_env(path), key, value)
    path.write_text(text, encoding="utf-8", errors="surrogateescape")


def warn_if_env_not_ignored(path: Path) -> None:
    for name in (SHARED_ENV, LOCAL_ENV):
        if capture(["git", "-C", str(path), "ls-files", "--", name]).stdout:
            continue  # Already in the repo, so ignoring it wouldn't help.
        # 1 means not ignored; anything else means git couldn't say.
        if capture(["git", "-C", str(path), "check-ignore", "-q", name]).returncode == 1:
            warn(f"{name} isn't gitignored, so what's in it could be committed. Add it to .gitignore.")
