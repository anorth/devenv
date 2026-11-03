"""Log and warning output, and the error that ends a command."""

import sys
from collections.abc import Callable


class Die(Exception):
    """Stop the command: the message goes to the user and the exit status is 1."""


def log(message: str) -> None:
    print(f"==> {message}", flush=True)


def warn(message: str) -> None:
    print(f"!!! {message}", file=sys.stderr, flush=True)


def guarded(command: Callable[[], None]) -> int:
    """Run a command to an exit status, reporting failures as `devenv:` errors."""
    # Child processes write straight to the terminal, so ours must keep up.
    sys.stdout.reconfigure(line_buffering=True)
    try:
        command()
    except (Die, OSError) as err:
        print(f"devenv: {err}", file=sys.stderr, flush=True)
        return 1
    except KeyboardInterrupt:
        return 130
    return 0
