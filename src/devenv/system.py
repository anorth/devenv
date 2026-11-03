"""Running external tools, always with argument lists, and reading the machine."""

import subprocess
from pathlib import Path
from typing import IO

from devenv import rules
from devenv.ui import Die


def capture(cmd: list[str], stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    """Run quietly and return the result. A missing command is an ordinary failure."""
    try:
        return subprocess.run(
            cmd,
            input=stdin,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="surrogateescape",
        )
    except FileNotFoundError:
        return subprocess.CompletedProcess(cmd, 127, "", f"{cmd[0]} isn't installed")


def call(cmd: list[str], cwd: Path | None = None, stdin: IO[bytes] | None = None) -> bool:
    """Run with output going to the terminal; say whether it succeeded."""
    try:
        return subprocess.run(cmd, cwd=cwd, stdin=stdin).returncode == 0
    except FileNotFoundError:
        return False


def listening_ports() -> set[int]:
    result = capture(["ss", "-Hltn"])
    if result.returncode != 0:
        raise Die(f"can't list listening ports with ss: {result.stderr.strip()}")
    return rules.parse_listening_ports(result.stdout)


def tailnet_name() -> str | None:
    """The machine's tailnet name, or None when it isn't on the tailnet."""
    result = capture(["tailscale", "status", "--json"])
    return rules.parse_tailnet_name(result.stdout) if result.returncode == 0 else None
