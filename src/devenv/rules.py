"""The naming, .env, port and parsing rules of the contract with the host environment.

Pure functions only: nothing here runs a command or touches the filesystem.
"""

import json
import re
from dataclasses import dataclass
from urllib.parse import parse_qs, unquote, urlsplit

FIRST_PORT = 3001
MAX_DB_NAME = 63
POSTGRES_PORT = "5432"
_LOCAL_HOSTS = {"", "localhost", "127.0.0.1"}
_POSTGRES_SCHEMES = {"postgres", "postgresql"}
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


def default_db(checkout_dir: str) -> str:
    """What `repo add --db` calls a checkout's own database."""
    return db_name(f"{checkout_dir}_dev")


def worktree_db(checkout_dir: str, name: str) -> str:
    return db_name(f"{checkout_dir}_{name}")


def database_url(db: str) -> str:
    return f"postgres://localhost/{db}"


def database_env(db: str, user: str) -> dict[str, str]:
    """The settings for a local database, for projects that want them broken out.

    The user is the OS user's role, which localhost TCP trusts without a password.
    """
    return {
        "DATABASE_URL": database_url(db),
        "DB_HOST": "localhost",
        "DB_PORT": POSTGRES_PORT,
        "DB_USER": user,
        "DB_PASSWORD": "",
        "DB_DATABASE": db,
    }


def database_from_url(url: str) -> str:
    try:
        path = urlsplit(url).path
    except ValueError:
        # Still the best guess, since `wt rm` won't drop a database that's named here.
        path = url.partition("?")[0]
    return unquote(path.rpartition("/")[2])


def databases_named(url: str, db_database: str) -> set[str]:
    """The databases that a DATABASE_URL and a DB_DATABASE name: more than one is a conflict."""
    return {name for name in (database_from_url(url), db_database) if name}


class DatabaseConflict(Exception):
    """DATABASE_URL and DB_DATABASE name different databases."""


def _is_local(host: str, port: str) -> bool:
    return host.lower() in _LOCAL_HOSTS and port in ("", POSTGRES_PORT)


def _url_is_local(url: str) -> bool:
    """Whether a URL reaches only the local Postgres. One that can't be read doesn't."""
    try:
        parts = urlsplit(url)
        port = str(parts.port or "")
    except ValueError:
        return False
    # Postgres lets the query string set the host and port, and it overrides the rest.
    query = parse_qs(parts.query)
    return (
        parts.scheme in _POSTGRES_SCHEMES
        and _is_local(parts.hostname or "", port)
        and all(_is_local(value, "") for value in query.get("host", []))
        and all(_is_local("", value) for value in query.get("port", []))
    )


@dataclass(frozen=True)
class NamedDatabase:
    name: str
    local: bool  # On the local Postgres, the only place pg_dump looks, for certain.


def named_database(url: str, db_database: str, db_host: str, db_port: str) -> NamedDatabase | None:
    """The database a checkout's settings name, if any.

    Raises DatabaseConflict if they disagree, because a guess could copy the wrong data.
    """
    names = databases_named(url, db_database)
    if len(names) > 1:
        raise DatabaseConflict(
            f"DATABASE_URL names {database_from_url(url)}, but DB_DATABASE names {db_database}"
        )
    if not names:
        return None
    local = (not url or _url_is_local(url)) and _is_local(db_host, db_port)
    return NamedDatabase(names.pop(), local)


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
