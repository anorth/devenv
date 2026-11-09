"""Worktree databases against the real Postgres; skipped when there isn't one."""

import shutil
import subprocess
import time
import uuid

import pytest

from conftest import git
from devenv import postgres



def sql(query):
    return subprocess.run(
        ["psql", "-X", "-At", "-d", "postgres", "-c", query], check=True, capture_output=True, text=True
    ).stdout.strip()


def database_names():
    return set(sql("SELECT datname FROM pg_database").splitlines())


@pytest.fixture
def repo(pg_machine):
    """A checkout with a uniquely named directory and the database it names."""
    name = f"t{uuid.uuid4().hex[:10]}"
    app = pg_machine.repo(name)
    template = f"{name}_dev"
    subprocess.run(["createdb", template], check=True)
    (app / ".env.local").write_text(f"DATABASE_URL=postgres://localhost/{template}\n")
    yield app, name, template
    for db in database_names():
        if db.startswith(name):
            subprocess.run(["dropdb", "--force", db], check=True)


def test_new_and_rm_create_and_drop_the_database(pg_machine, repo):
    app, name, _ = repo
    pg_machine.ok("wt", "new", "feat/x", cwd=app)
    db = f"{name}_feat_x"
    assert db in database_names()
    env = (pg_machine.home / "feat-x/.env.local").read_text()
    assert f"DB_DATABASE={name}_feat_x\n" in env
    assert "DB_HOST=localhost\nDB_PORT=5432\n" in env
    assert "DB_PASSWORD=\n" in env
    assert f"DATABASE_URL=postgres://localhost/{db}\n" in env

    pg_machine.ok("wt", "rm", "feat/x", cwd=app)
    assert db not in database_names()


def test_rm_leaves_a_database_the_env_doesnt_name(pg_machine, repo):
    app, name, _ = repo
    pg_machine.ok("wt", "new", "a", cwd=app)
    env = pg_machine.home / "a/.env.local"
    env.write_text("DATABASE_URL=postgres://localhost/something_else\n")

    result = pg_machine.ok("wt", "rm", "a", cwd=app)
    assert f"!!! leaving database {name}_a: this worktree's env files name something_else." in result.stderr
    assert f"{name}_a" in database_names()


def test_locked_worktree_keeps_its_database(pg_machine, repo):
    app, name, _ = repo
    pg_machine.ok("wt", "new", "a", cwd=app)
    git(app, "worktree", "lock", str(pg_machine.home / "a"))
    result = pg_machine.run("wt", "rm", "a", cwd=app)
    assert result.returncode == 1
    assert "git worktree remove failed" in result.stderr
    assert f"{name}_a" in database_names()


def test_rm_drops_a_database_named_only_by_db_database(pg_machine, repo):
    app, name, _ = repo
    pg_machine.ok("wt", "new", "a", cwd=app)
    env = pg_machine.home / "a/.env.local"
    env.write_text(f"PORT=3001\nDB_DATABASE={name}_a\n")
    assert f"{name}_a" in pg_machine.ok("wt", "ls", cwd=app).stdout
    pg_machine.ok("wt", "rm", "a", cwd=app)
    assert f"{name}_a" not in database_names()


def test_rm_leaves_a_database_when_the_settings_disagree(pg_machine, repo):
    app, name, _ = repo
    pg_machine.ok("wt", "new", "a", cwd=app)
    env = pg_machine.home / "a/.env.local"
    env.write_text(f"DATABASE_URL=postgres://localhost/{name}_a\nDB_DATABASE=other\n")
    result = pg_machine.ok("wt", "rm", "a", cwd=app)
    assert f"!!! leaving database {name}_a: this worktree's env files name other, {name}_a." in result.stderr
    assert f"{name}_a" in database_names()
    subprocess.run(["dropdb", f"{name}_a"], check=True)


def test_name_of_the_template_is_refused(pg_machine, repo):
    app, name, template = repo
    result = pg_machine.run("wt", "new", "dev", cwd=app)
    assert result.returncode == 1
    assert f"would use the name of the template database, {template}" in result.stderr
    assert "--branch" in result.stderr
    assert template in database_names()


def test_branch_named_after_the_template_can_take_another_name(pg_machine, repo):
    app, name, _ = repo
    pg_machine.ok("wt", "new", "other", "--branch", "dev", cwd=app)
    assert f"{name}_other" in database_names()
    pg_machine.ok("wt", "rm", "other", cwd=app)


def test_database_stays_with_the_name_across_branches(pg_machine, repo):
    app, name, _ = repo
    pg_machine.ok("wt", "new", "long", "--branch", "first", cwd=app)
    path = pg_machine.home / "long"
    git(path, "switch", "-qc", "second")
    assert f"DATABASE_URL=postgres://localhost/{name}_long\n" in (path / ".env.local").read_text()
    pg_machine.ok("wt", "rm", "long", cwd=app)
    assert f"{name}_long" not in database_names()


@pytest.mark.parametrize(
    "main_env",
    [
        "DATABASE_URL=postgres://localhost/{db}\n",
        "DB_DATABASE={db}\n",
        "DATABASE_URL=postgres://localhost/{db}\nDB_DATABASE=other\n",
        "DATABASE_URL=postgres://localhost/other\nDB_DATABASE={db}\n",
    ],
)
def test_rm_never_drops_what_the_main_checkout_names(pg_machine, main_env):
    name = f"t{uuid.uuid4().hex[:10]}"
    app = pg_machine.repo(name)
    template = f"{name}_dev"
    pg_machine.ok("wt", "new", "dev", cwd=app)  # before the template exists
    subprocess.run(["createdb", template], check=True)
    # The worktree claims it too, which would otherwise be enough to drop it.
    (pg_machine.home / "dev/.env.local").write_text(f"DB_DATABASE={template}\n")
    (app / ".env.local").write_text(main_env.format(db=template))
    try:
        pg_machine.ok("wt", "rm", "dev", cwd=app)
        assert template in database_names()
    finally:
        subprocess.run(["dropdb", "--if-exists", template], check=True)


def test_template_with_an_unconventional_name(pg_machine, repo):
    app, name, _ = repo
    template = f"{name} Odd-Name"
    subprocess.run(["createdb", template], check=True)
    fill_template(template)
    encoded = template.replace(" ", "%20")
    (app / ".env.local").write_text(f"DATABASE_URL=postgres://localhost/{encoded}?schema=public\n")
    pg_machine.ok("wt", "new", "a", cwd=app)
    assert rows(f"{name}_a") == "7"
    pg_machine.ok("wt", "rm", "a", cwd=app)
    assert template in database_names()
    assert f"{name}_a" not in database_names()


def test_local_env_overrides_the_shared_one(pg_machine, repo):
    app, name, template = repo
    subprocess.run(["createdb", f"{name}_real"], check=True)
    fill_template(f"{name}_real")
    (app / ".env").write_text(f"DB_DATABASE={template}\n")
    (app / ".env.local").write_text(f"DB_DATABASE={name}_real\n")
    pg_machine.ok("wt", "new", "a", cwd=app)
    assert rows(f"{name}_a") == "7"
    pg_machine.ok("wt", "rm", "a", cwd=app)


def test_refuses_existing_database(pg_machine, repo):
    app, name, _ = repo
    subprocess.run(["createdb", f"{name}_a"], check=True)
    result = pg_machine.run("wt", "new", "a", cwd=app)
    assert result.returncode == 1
    assert f"database {name}_a already exists. If nothing uses it, drop it with: dropdb {name}_a" in result.stderr
    assert not (pg_machine.home / "a").exists()


def hold_connection(db):
    holder = subprocess.Popen(
        ["psql", "-X", "-d", db, "-c", "SELECT pg_sleep(30)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    query = f"SELECT count(*) FROM pg_stat_activity WHERE datname = '{db}'"
    for _ in range(50):
        if sql(query) != "0":
            return holder
        time.sleep(0.1)
    holder.kill()
    pytest.fail("the holding connection never appeared")


def rows(db):
    """What fill_template put in a database, or a copy of one."""
    return subprocess.run(
        ["psql", "-X", "-At", "-d", db, "-c", "SELECT n FROM t"], check=True, capture_output=True, text=True
    ).stdout.strip()


def fill_template(template):
    subprocess.run(
        ["psql", "-X", "-q", "-d", template, "-c", "CREATE TABLE t (n int PRIMARY KEY); INSERT INTO t VALUES (7)"],
        check=True,
    )


def test_copies_the_data(pg_machine, repo):
    app, name, template = repo
    fill_template(template)
    pg_machine.ok("wt", "new", "a", cwd=app)
    assert rows(f"{name}_a") == "7"


def test_copies_while_the_template_is_in_use(pg_machine, repo):
    app, name, template = repo
    fill_template(template)
    holder = hold_connection(template)
    try:
        pg_machine.ok("wt", "new", "a", cwd=app)
    finally:
        holder.kill()
        holder.wait()
    assert f"{name}_a" in database_names()
    assert (pg_machine.home / "a").exists()


@pytest.mark.parametrize("tool", ["pg_dump", "pg_restore"])
def test_failed_copy_leaves_nothing_behind(pg_machine, repo, tool):
    app, name, template = repo
    pg_machine.stub(tool, "cat >/dev/null; echo boom >&2; exit 1")
    result = pg_machine.run("wt", "new", "a", cwd=app)
    assert result.returncode == 1
    assert f"can't copy {template}: pg_dump or pg_restore failed" in result.stderr
    assert f"{name}_a" not in database_names()
    assert not (pg_machine.home / "a").exists()


def test_rm_refuses_database_with_connections_unless_forced(pg_machine, repo):
    app, name, _ = repo
    pg_machine.ok("wt", "new", "a", cwd=app)
    db = f"{name}_a"
    holder = hold_connection(db)
    try:
        result = pg_machine.run("wt", "rm", "a", cwd=app)
        assert result.returncode == 1
        assert f"database {db} has 1 active connection(s)" in result.stderr
        assert (pg_machine.home / "a").exists()

        pg_machine.ok("wt", "rm", "a", "--force", cwd=app)
    finally:
        holder.kill()
        holder.wait()
    assert db not in database_names()


def test_failed_worktree_add_drops_the_database(pg_machine, repo):
    app, name, _ = repo
    # Fail only the worktree add, after the database has been made.
    git_stub = pg_machine.bin / "git"
    real_git = shutil.which("git")
    git_stub.write_text(
        f'#!/bin/sh\ncase "$*" in *"worktree add"*) exit 1;; esac\nexec {real_git} "$@"\n'
    )
    git_stub.chmod(0o755)

    result = pg_machine.run("wt", "new", "a", cwd=app)
    assert result.returncode == 1
    assert "git worktree add failed" in result.stderr
    assert f"{name}_a" not in database_names()


def test_names_reach_sql_as_values(pg_machine):
    assert not postgres.db_exists("it's'; DROP DATABASE postgres; --")
    assert postgres.db_connections("it's") == 0
    assert postgres.db_exists("postgres")
