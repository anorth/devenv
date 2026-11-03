"""The local Postgres. Names reach SQL only as psql variables."""

import subprocess

from devenv.system import call, capture
from devenv.ui import Die, warn


def _query(sql: str, name: str) -> str:
    # The SQL goes on stdin because psql doesn't expand variables in -c.
    result = capture(
        ["psql", "-X", "-q", "-At", "-v", "ON_ERROR_STOP=1", "-v", f"name={name}", "-d", "postgres"],
        stdin=sql,
    )
    if result.returncode != 0:
        raise Die("can't query Postgres; is it running? (sudo systemctl status postgresql)")
    return result.stdout.strip()


def db_exists(name: str) -> bool:
    # Dies if Postgres can't be queried: treating that as "no such database"
    # would leave a worktree silently using the main checkout's database.
    return _query("SELECT 1 FROM pg_database WHERE datname = :'name'", name) == "1"


def db_connections(name: str) -> int:
    return int(_query("SELECT count(*) FROM pg_stat_activity WHERE datname = :'name'", name))


def _dump_and_restore(source: str, target: str) -> bool:
    try:
        dump = subprocess.Popen(["pg_dump", "-Fc", "-d", source], stdout=subprocess.PIPE)
    except FileNotFoundError:
        return False
    with dump:
        restored = call(["pg_restore", "--exit-on-error", "-d", target], stdin=dump.stdout)
    return restored and dump.returncode == 0


def copy_database(source: str, target: str) -> None:
    """Copy a database, leaving nothing behind if that fails or is interrupted.

    A dump reads one consistent snapshot, so unlike createdb -T it works while
    the dev server is connected to the source. template0 starts the copy empty,
    so nothing added to template1 can clash with the restore.
    """
    created = capture(["createdb", "-T", "template0", target])
    if created.returncode != 0:
        raise Die(f"createdb failed: {created.stderr.strip()}")
    copied = False
    try:
        copied = _dump_and_restore(source, target)
    finally:
        if not copied and not drop(target):
            warn(f"couldn't drop {target}; drop it with: dropdb {target}")
    if not copied:
        raise Die(f"can't copy {source}: pg_dump or pg_restore failed")


def create(name: str) -> bool:
    return call(["createdb", name])


def drop(name: str, force: bool = False) -> bool:
    return call(["dropdb", *(["--force"] if force else []), name])
