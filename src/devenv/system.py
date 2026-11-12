"""Running external tools, always with argument lists, and reading the machine."""

import subprocess
from pathlib import Path
from typing import IO

from devenv import rules
from devenv.ui import Die


def capture(
    cmd: list[str], stdin: str | None = None, cwd: Path | None = None
) -> subprocess.CompletedProcess[str]:
    """Run quietly and return the result. A missing command is an ordinary failure."""
    try:
        return subprocess.run(
            cmd,
            cwd=cwd,
            input=stdin,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="surrogateescape",
        )
    except FileNotFoundError:
        return subprocess.CompletedProcess(cmd, 127, "", f"{cmd[0]} isn't installed")


def call(
    cmd: list[str],
    cwd: Path | None = None,
    stdin: IO[bytes] | None = None,
    env: dict[str, str] | None = None,
    detach: bool = False,
) -> bool:
    """Run with output going to the terminal; say whether it succeeded.

    `detach` runs it in a new session, with no controlling terminal, so it
    can't open /dev/tty to prompt. Its output still reaches the terminal.

    Failing to start counts as failing: a command can be missing, or a script
    can lack the shebang it needs to be executed.
    """
    try:
        result = subprocess.run(cmd, cwd=cwd, stdin=stdin, env=env, start_new_session=detach)
        return result.returncode == 0
    except OSError:
        return False


def status(cmd: list[str]) -> int:
    """Run with output going to the terminal, and return its exit status."""
    try:
        return subprocess.run(cmd).returncode
    except OSError as err:
        raise Die(f"can't run {cmd[0]}: {err}") from None


def listening_ports() -> set[int]:
    result = capture(["ss", "-Hltn"])
    if result.returncode != 0:
        raise Die(f"can't list listening ports with ss: {result.stderr.strip()}")
    return rules.parse_listening_ports(result.stdout)


def tailnet_name() -> str | None:
    """The machine's tailnet name, or None when it isn't on the tailnet."""
    result = capture(["tailscale", "status", "--json"])
    return rules.parse_tailnet_name(result.stdout) if result.returncode == 0 else None
