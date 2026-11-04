"""The naming, .env, port and parsing rules of the contract with the host environment.

Pure functions only: nothing here runs a command or touches the filesystem.
"""

import json
import re
from dataclasses import dataclass

FIRST_PORT = 3001
MAX_DB_NAME = 63
_DIR_PART = r"[A-Za-z0-9_][A-Za-z0-9_.-]*"


def repo_name(clone_url: str) -> str:
    """A repo's name from an https://, file:// or git@host:owner/name clone URL."""
    return re.split(r"[/:]", clone_url.rstrip("/"))[-1].removesuffix(".git")


def valid_checkout_dir(path: str) -> bool:
    """Whether a path under home fits the layout: <dir> or <group>/<dir>."""
    return re.fullmatch(rf"({_DIR_PART}/)?{_DIR_PART}", path) is not None


def slugify(text: str) -> str:
    """Lowercase, with runs of anything outside [a-z0-9] collapsed to one hyphen."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def db_name(text: str) -> str:
    """A Postgres database name: [a-z0-9_], at most 63 characters."""
    return re.sub(r"[^a-z0-9_]", "_", text.lower())[:MAX_DB_NAME]


def template_db(checkout_dir: str) -> str:
    return db_name(f"{checkout_dir}_dev")


def worktree_db(checkout_dir: str, name: str) -> str:
    return db_name(f"{checkout_dir}_{name}")


def database_url(db: str) -> str:
    return f"postgres://localhost/{db}"


def database_from_url(url: str) -> str:
    return url.rsplit("/", 1)[-1].split("?", 1)[0]


def _without_export(line: str) -> str:
    return re.sub(r"^export[ \t]+", "", line)


def env_get(text: str, key: str) -> str:
    """KEY's value in .env text, without quotes; the last assignment wins."""
    value = ""
    for line in text.split("\n"):
        line = _without_export(line)
        if line.startswith(f"{key}="):
            unquoted = re.sub(r"[\"']", "", line[len(key) + 1 :])
            value = re.split(r"[ \t]", unquoted, maxsplit=1)[0]
    return value


def env_set(text: str, key: str, value: str) -> str:
    """.env text with KEY=value replacing every assignment of KEY, or appended."""
    assignment = f"{key}={value}"
    lines = []
    done = False
    body = text.rstrip("\n")
    for line in body.split("\n") if body else []:
        if _without_export(line).startswith(f"{key}="):
            if not done:
                lines.append(assignment)
                done = True
        else:
            lines.append(line)
    if not done:
        lines.append(assignment)
    return "\n".join(lines) + "\n"


def choose_port(used: set[int]) -> int | None:
    """The lowest port from 3001 not in `used`, or None if there isn't one."""
    return next((p for p in range(FIRST_PORT, 65536) if p not in used), None)


def parse_listening_ports(ss_output: str) -> set[int]:
    """Ports in the output of `ss -Hltn`, whose fourth column is address:port."""
    ports = set()
    for line in ss_output.splitlines():
        fields = line.split()
        if len(fields) >= 4:
            port = fields[3].rsplit(":", 1)[-1]
            if port.isdecimal():
                ports.add(int(port))
    return ports


@dataclass(frozen=True)
class Worktree:
    path: str
    branch: str | None  # None when detached


def parse_worktree_list(porcelain: str) -> list[Worktree]:
    """Worktrees from `git worktree list --porcelain`, the main checkout first."""
    worktrees = []
    for record in porcelain.split("\n\n"):
        path = branch = None
        for line in record.splitlines():
            if line.startswith("worktree "):
                path = line.removeprefix("worktree ")
            elif line.startswith("branch "):
                branch = line.removeprefix("branch ").removeprefix("refs/heads/")
        if path is not None:
            worktrees.append(Worktree(path, branch))
    return worktrees


def parse_tailnet_name(status_json: str) -> str | None:
    """This machine's tailnet name from `tailscale status --json`.

    Only Running means the name resolves now.
    """
    try:
        status = json.loads(status_json)
        if status["BackendState"] != "Running":
            return None
        return status["Self"]["DNSName"].split(".")[0] or None
    except (ValueError, KeyError, TypeError, AttributeError):
        return None
