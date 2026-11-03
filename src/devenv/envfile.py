"""Reading and writing .env files, one of the contract's kinds of state."""

from pathlib import Path

from devenv import rules


def read_env(path: Path) -> str:
    """A .env file's text; a missing one reads as empty."""
    try:
        return path.read_text(encoding="utf-8", errors="surrogateescape")
    except FileNotFoundError:
        return ""


def env_file_get(path: Path, key: str) -> str:
    return rules.env_get(read_env(path), key)


def env_file_set(path: Path, key: str, value: str) -> None:
    text = rules.env_set(read_env(path), key, value)
    path.write_text(text, encoding="utf-8", errors="surrogateescape")
