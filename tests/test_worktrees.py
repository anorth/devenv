import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from conftest import git, git_out


def env_of(path):
    return (path / ".env.local").read_text()


def test_new_branch_from_default(machine):
    app = machine.repo(origin=True)
    result = machine.ok("wt", "new", "feat/x", cwd=app)
    path = machine.home / "feat-x"
    assert (path / "README").exists()
    assert "PORT=3001\n" in env_of(path)
    assert git_out(path, "branch", "--show-current") == "feat/x"
    # A new branch doesn't track its base, so a push can't land on main.
    assert git_out(path, "for-each-ref", "--format=%(upstream)", "refs/heads/feat/x") == ""
    assert f"Worktree ready: {path}" in result.stdout
    assert "http://localhost:3001 (inside the VM: it isn't on the tailnet)" in result.stdout
    assert "(none of its own)" in result.stdout
    assert "Open it:" not in result.stdout


def test_new_from_local_branch(machine):
    app = machine.repo()
    git(app, "branch", "topic")
    machine.ok("wt", "new", "topic", cwd=app)
    assert git_out(machine.home / "topic", "branch", "--show-current") == "topic"


def test_new_from_origin_only_branch(machine):
    app = machine.repo(origin=True)
    seed = machine.home.parent / "seeds/app"
    git(seed, "checkout", "-qb", "remote-only")
    (seed / "extra").write_text("x\n")
    git(seed, "add", ".")
    git(seed, "commit", "-qm", "extra")
    git(seed, "push", "-q", "origin", "remote-only")

    machine.ok("wt", "new", "remote-only", cwd=app)
    path = machine.home / "remote-only"
    assert (path / "extra").exists()
    upstream = git_out(path, "for-each-ref", "--format=%(upstream:short)", "refs/heads/remote-only")
    assert upstream == "origin/remote-only"


def test_repo_without_origin_skips_the_fetch(machine):
    app = machine.repo()
    (app / ".env").touch()
    created = machine.ok("wt", "new", "a", cwd=app)
    removed = machine.ok("wt", "rm", "a", cwd=app)
    assert "Fetching origin" not in created.stdout
    for output in (created.stderr, removed.stderr):
        assert "fatal" not in output and "!!!" not in output


def test_base_selects_the_start(machine):
    app = machine.repo()
    git(app, "checkout", "-qb", "develop")
    (app / "dev-only").write_text("x\n")
    git(app, "add", ".")
    git(app, "commit", "-qm", "dev")
    git(app, "checkout", "-q", "main")

    machine.ok("wt", "new", "feat", "--base", "develop", cwd=app)
    assert (machine.home / "feat/dev-only").exists()


def test_base_ignored_for_existing_branch(machine):
    app = machine.repo()
    git(app, "branch", "topic")
    result = machine.ok("wt", "new", "topic", "--base", "main", cwd=app)
    assert "!!! branch topic already exists, so --base main is ignored" in result.stderr


def test_missing_base(machine):
    app = machine.repo()
    result = machine.run("wt", "new", "x", "--base", "nope", cwd=app)
    assert result.returncode == 1
    assert result.stderr.endswith("devenv: base branch nope not found locally or on origin\n")
    assert not (machine.home / "x").exists()


def test_new_from_inside_a_worktree(machine):
    app = machine.repo()
    machine.ok("wt", "new", "one", cwd=app)
    subdir = machine.home / "one/sub"
    subdir.mkdir()
    machine.ok("wt", "new", "two", cwd=subdir)
    path = machine.home / "two"
    assert "PORT=3002\n" in env_of(path)


def test_nested_checkout(machine):
    app = machine.repo("group/repo")
    machine.ok("wt", "new", "feat/x", cwd=app)
    path = machine.home / "group/feat-x"
    assert (path / "README").exists()
    assert not (machine.home / "feat-x").exists()


def test_port_skips_one_claimed_by_another_checkout(machine):
    first = machine.repo("first")
    nested = machine.repo("group/second")
    machine.ok("wt", "new", "a", cwd=first)
    machine.ok("wt", "new", "b", cwd=nested)
    assert "PORT=3002\n" in env_of(machine.home / "group/b")


def test_port_claimed_by_main_checkout_env(machine):
    app = machine.repo()
    (app / ".env").write_text("export PORT='3001'\n")
    machine.ok("wt", "new", "a", cwd=app)
    assert "PORT=3002\n" in env_of(machine.home / "a")


def test_port_skips_listening_socket(machine):
    (machine.bin / "ss-output").write_text("LISTEN 0 511 *:3001 *:*\n")
    app = machine.repo()
    machine.ok("wt", "new", "a", cwd=app)
    assert "PORT=3002\n" in env_of(machine.home / "a")


def test_env_is_linked_and_local_env_is_the_worktrees_own(machine):
    app = machine.repo("group/app")
    (app / ".env").write_text("SECRET=keep\nPORT=9\nDATABASE_URL=postgres://elsewhere/shared\n")
    result = machine.ok("wt", "new", "a", cwd=app)
    path = machine.home / "group/a"
    assert (path / ".env").readlink() == Path("../app/.env")
    assert env_of(path) == "PORT=3001\n"
    assert "uses the settings in its .env" in result.stdout


def test_db_fields_alone_are_recognised(machine):
    app = machine.repo()
    (app / ".env").write_text("DB_HOST=db.example.com\nDB_DATABASE=shared\n")
    result = machine.ok("wt", "new", "a", cwd=app)
    assert "uses the settings in its .env" in result.stdout
    row = next(r for r in machine.ok("wt", "ls", cwd=app).stdout.splitlines() if r.startswith("a "))
    assert "shared" in row


def test_a_changed_secret_reaches_every_worktree(machine):
    app = machine.repo()
    (app / ".env").write_text("SECRET=old\n")
    machine.ok("wt", "new", "a", cwd=app)
    (app / ".env").write_text("SECRET=new\n")
    assert (machine.home / "a/.env").read_text() == "SECRET=new\n"


def test_rm_leaves_the_main_env_alone(machine):
    app = machine.repo()
    (app / ".env").write_text("SECRET=keep\n")
    machine.ok("wt", "new", "a", cwd=app)
    machine.ok("wt", "rm", "a", cwd=app)
    assert (app / ".env").read_text() == "SECRET=keep\n"


def test_missing_main_env_warns(machine):
    app = machine.repo()
    result = machine.ok("wt", "new", "a", cwd=app)
    assert f"!!! {app}/.env doesn't exist" in result.stderr
    assert not (machine.home / "a/.env").is_symlink()
    assert env_of(machine.home / "a") == "PORT=3001\n"


def test_tracked_env_is_left_alone(machine):
    app = machine.repo()
    git(app, "config", "core.excludesFile", "/dev/null")
    (app / ".env").write_text("DEFAULT=1\n")
    git(app, "add", ".")
    git(app, "commit", "-qm", "env")
    result = machine.ok("wt", "new", "a", cwd=app)
    path = machine.home / "a"
    assert not (path / ".env").is_symlink()
    assert (path / ".env").read_text() == "DEFAULT=1\n"
    assert "the repo has its own .env" in result.stderr
    assert "!!! .env isn't gitignored" not in result.stderr


def test_warns_when_env_files_are_not_ignored(machine):
    app = machine.repo()
    git(app, "config", "core.excludesFile", "/dev/null")
    (app / ".env").touch()
    result = machine.ok("wt", "new", "a", cwd=app)
    assert "!!! .env isn't gitignored" in result.stderr
    assert "!!! .env.local isn't gitignored" in result.stderr
    # Not ignored, but still not changes that block removing it.
    machine.ok("wt", "rm", "a", cwd=app)


def test_values_resolve_as_the_dev_server_would(machine):
    app = machine.repo()
    (app / ".env").write_text("PORT=3001\nDATABASE_URL=postgres://localhost/shared\n")
    (app / ".env.local").write_text("PORT=3002\n")
    row = machine.ok("wt", "ls", cwd=app).stdout.splitlines()[1]
    assert "3002" in row and "shared" in row
    # The main checkout's port is 3002: its shared 3001 is overridden, so it's free.
    machine.ok("wt", "new", "a", cwd=app)
    machine.ok("wt", "new", "b", cwd=app)
    assert env_of(machine.home / "a") == "PORT=3001\n"
    assert env_of(machine.home / "b") == "PORT=3003\n"


def test_worktree_from_before_the_split_keeps_its_values(machine):
    app = machine.repo()
    machine.ok("wt", "new", "a", cwd=app)
    path = machine.home / "a"
    (path / ".env.local").unlink()
    (path / ".env").write_text("PORT=3005\n")
    assert "3005" in machine.ok("wt", "ls", cwd=app).stdout


def test_tailnet_summary(machine):
    status = json.dumps({"BackendState": "Running", "Self": {"DNSName": "box.tail.ts.net."}})
    machine.stub("tailscale", f"echo '{status}'")
    app = machine.repo()
    result = machine.ok("wt", "new", "a", cwd=app)
    path = machine.home / "a"
    assert "http://box:3001\n" in result.stdout
    assert "Open it:" in result.stdout
    assert re.search(rf"cursor --remote ssh-remote\+\S+@box {path}\n", result.stdout)
    assert re.search(rf"zed ssh://\S+@box{path}\n", result.stdout)


def test_refuses_existing_path(machine):
    app = machine.repo()
    (machine.home / "a").mkdir(parents=True)
    result = machine.run("wt", "new", "a", cwd=app)
    assert result.returncode == 1
    assert "already exists. Pick another name." in result.stderr


def test_refuses_branch_checked_out_elsewhere(machine):
    app = machine.repo()
    result = machine.run("wt", "new", "main", cwd=app)
    assert result.returncode == 1
    assert f"devenv: branch main is already checked out at {app}\n" in result.stderr


def test_refuses_bad_branch_name(machine):
    app = machine.repo()
    result = machine.run("wt", "new", "a..b", cwd=app)
    assert result.returncode == 1
    assert "not a valid branch name: a..b" in result.stderr


def record_database_tools(machine, existing="(none)"):
    """Make `existing` the only database, and log every use of a Postgres tool."""
    log = machine.home / "pg-calls"
    machine.stub("psql", f"""cat >/dev/null; echo psql "$@" >>"{log}"
case "$*" in *"name={existing}"*) echo 1;; esac""")
    for tool in ("createdb", "pg_dump", "pg_restore"):
        machine.stub(tool, f'echo {tool} "$@" >>"{log}"')
    return log


def test_disagreement_stops_with_nothing_created(machine):
    log = record_database_tools(machine)
    app = machine.repo()
    (app / ".env").write_text("DATABASE_URL=postgres://localhost/one\n")
    (app / ".env.local").write_text("DB_DATABASE=two\n")
    result = machine.run("wt", "new", "a", cwd=app)
    assert result.returncode == 1
    assert "DATABASE_URL names one, but DB_DATABASE names two. Make them agree." in result.stderr
    assert not (machine.home / "a").exists()
    assert "refs/heads/a" not in git_out(app, "for-each-ref")
    assert not log.exists()


def test_no_fallback_to_the_conventional_name(machine):
    log = record_database_tools(machine, existing="app_dev")
    app = machine.repo()
    result = machine.ok("wt", "new", "a", cwd=app)
    assert "(none of its own)" in result.stdout
    assert "name database" not in result.stderr
    assert "DATABASE_URL" not in env_of(machine.home / "a")
    assert not log.exists()


def test_new_worktree_may_be_called_dev_if_thats_not_the_template(machine):
    record_database_tools(machine, existing="app_dev")
    app = machine.repo()
    machine.ok("wt", "new", "dev", cwd=app)


@pytest.mark.parametrize(
    "file, settings, shared",
    [
        (".env", "DATABASE_URL=postgres://db.example.com/shared\n", True),
        (".env", "DATABASE_URL=postgres://localhost/shared\nDB_HOST=db.example.com\n", True),
        (".env.local", "DATABASE_URL=postgres://db.example.com/shared\n", False),
    ],
)
def test_remote_database_is_not_copied(machine, file, settings, shared):
    log = record_database_tools(machine, existing="shared")
    app = machine.repo()
    (app / ".env").write_text("SECRET=keep\n")
    with (app / file).open("a") as env:
        env.write(settings)
    result = machine.ok("wt", "new", "a", cwd=app)
    assert "DATABASE_URL" not in env_of(machine.home / "a")
    assert not log.exists()
    assert "name database shared, which isn't on the local Postgres" in result.stderr
    assert ("uses the settings in its .env" in result.stdout) == shared
    assert "(none of its own" in result.stdout


def test_named_but_missing_database_warns(machine):
    log = record_database_tools(machine)
    app = machine.repo()
    (app / ".env.local").write_text("DB_DATABASE=gone\n")
    result = machine.ok("wt", "new", "a", cwd=app)
    assert "name database gone, which doesn't exist" in result.stderr
    assert "(none of its own" in result.stdout
    assert "DATABASE_URL" not in env_of(machine.home / "a")
    assert "createdb" not in log.read_text()


def test_dies_when_postgres_cant_be_queried(machine):
    machine.stub("psql", "cat >/dev/null; exit 2")
    app = machine.repo()
    (app / ".env.local").write_text("DB_DATABASE=app_main\n")
    result = machine.run("wt", "new", "a", cwd=app)
    assert result.returncode == 1
    assert "can't query Postgres; is it running?" in result.stderr
    assert not (machine.home / "a").exists()


def test_filesystem_errors_are_reported_without_a_traceback(machine):
    app = machine.repo()
    (app / ".env.local").mkdir()
    (app / ".env.local/x").touch()
    git(app, "add", "-f", ".")
    git(app, "commit", "-qm", "a directory where the worktree's .env.local goes")
    result = machine.run("wt", "new", "a", cwd=app)
    assert result.returncode == 1
    assert "devenv: " in result.stderr
    assert "Traceback" not in result.stderr


def test_outside_a_checkout(machine):
    result = machine.run("wt", "ls")
    assert result.returncode == 1
    assert "devenv: run devenv inside a git checkout" in result.stderr


def test_refuses_bare_repo(machine):
    bare = machine.home / "bare.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True, env=machine.env)
    result = machine.run("wt", "new", "a", cwd=bare)
    assert result.returncode == 1
    assert "bare repos, submodules and --separate-git-dir clones aren't supported" in result.stderr


def test_rm_refuses_dirty_worktree(machine):
    app = machine.repo()
    machine.ok("wt", "new", "a", cwd=app)
    path = machine.home / "a"
    (path / "new-file").write_text("x\n")

    result = machine.run("wt", "rm", "a", cwd=app)
    assert result.returncode == 1
    assert "has uncommitted changes (use --force to remove anyway):\n?? new-file\n" in result.stderr
    assert path.exists()

    machine.ok("wt", "rm", "a", "--force", cwd=app)
    assert not path.exists()


def test_rm_ignores_untracked_env(machine):
    app = machine.repo()
    machine.ok("wt", "new", "a", cwd=app)
    machine.ok("wt", "rm", "a", cwd=app)
    assert not (machine.home / "a").exists()


def test_rm_keeps_unmerged_branch(machine):
    app = machine.repo()
    machine.ok("wt", "new", "a", cwd=app)
    path = machine.home / "a"
    (path / "work").write_text("x\n")
    git(path, "add", "work")
    git(path, "commit", "-qm", "work")

    result = machine.ok("wt", "rm", "a", cwd=app)
    assert "Keeping branch a: not merged into main" in result.stdout
    assert git_out(app, "branch", "--list", "a") != ""


def test_rm_deletes_merged_branch(machine):
    app = machine.repo()
    machine.ok("wt", "new", "a", cwd=app)
    result = machine.ok("wt", "rm", "a", cwd=app)
    assert "Deleting branch a (merged into main)" in result.stdout
    assert git_out(app, "branch", "--list", "a") == ""


def test_rm_deletes_branch_merged_on_origin(machine):
    app = machine.repo(origin=True)
    machine.ok("wt", "new", "a", cwd=app)
    path = machine.home / "a"
    (path / "work").write_text("x\n")
    git(path, "add", "work")
    git(path, "commit", "-qm", "work")
    git(path, "push", "-q", "origin", "HEAD:main")

    result = machine.ok("wt", "rm", "a", cwd=app)
    assert "Deleting branch a (merged into main)" in result.stdout


def test_rm_from_inside_the_worktree(machine):
    app = machine.repo()
    machine.ok("wt", "new", "a", cwd=app)
    path = machine.home / "a"
    result = machine.ok("wt", "rm", "a", cwd=path)
    assert not path.exists()
    assert f"Your shell is still in the removed worktree. Leave it with: cd {app}" in result.stdout


def test_rm_from_outside_says_nothing_about_the_shell(machine):
    app = machine.repo()
    machine.ok("wt", "new", "a", cwd=app)
    assert "Your shell" not in machine.ok("wt", "rm", "a", cwd=app).stdout


def test_rm_of_a_worktree_whose_directory_is_gone(machine):
    app = machine.repo()
    machine.ok("wt", "new", "a", cwd=app)
    shutil.rmtree(machine.home / "a")
    machine.ok("wt", "rm", "a", cwd=app)
    assert git_out(app, "worktree", "list").count("\n") == 0


def test_rm_refuses_main_checkout(machine):
    app = machine.repo()
    result = machine.run("wt", "rm", "app", cwd=app)
    assert result.returncode == 1
    assert "devenv: refusing to remove the main checkout\n" in result.stderr


def test_rm_unknown_name(machine):
    app = machine.repo()
    result = machine.run("wt", "rm", "nope", cwd=app)
    assert result.returncode == 1
    assert "no worktree is named nope (devenv wt ls)" in result.stderr


def test_name_sets_the_directory(machine):
    app = machine.repo()
    machine.ok("wt", "new", "Long Running", "--branch", "feat/x", cwd=app)
    assert git_out(machine.home / "long-running", "branch", "--show-current") == "feat/x"


def test_branch_defaults_to_the_name_as_typed(machine):
    app = machine.repo()
    machine.ok("wt", "new", "feat/x", cwd=app)
    assert git_out(machine.home / "feat-x", "branch", "--show-current") == "feat/x"


def test_name_that_cannot_be_a_branch_needs_one(machine):
    app = machine.repo()
    result = machine.run("wt", "new", "Long Running", cwd=app)
    assert result.returncode == 1
    assert "not a valid branch name: Long Running. Choose one with --branch." in result.stderr


def test_group_worktrees_are_peers_of_the_checkout(machine):
    app = machine.repo("group/app")
    machine.ok("wt", "new", "a", cwd=app)
    assert (machine.home / "group/a").is_dir()


def test_refuses_a_name_taken_by_a_peer(machine):
    app = machine.repo()
    machine.repo("other")
    result = machine.run("wt", "new", "other", "--branch", "a", cwd=app)
    assert result.returncode == 1
    assert "Pick another name" in result.stderr


def test_refuses_the_main_checkout_as_a_name(machine):
    app = machine.repo()
    result = machine.run("wt", "new", "app", cwd=app)
    assert result.returncode == 1
    assert f"{app} already exists" in result.stderr


def test_rm_takes_the_name_ls_shows_for_a_hand_made_worktree(machine):
    app = machine.repo()
    git(app, "worktree", "add", "-q", "-b", "a", str(machine.home / "My_WT"))
    assert "My_WT" in machine.ok("wt", "ls", cwd=app).stdout
    machine.ok("wt", "rm", "My_WT", cwd=app)
    assert not (machine.home / "My_WT").exists()


def test_a_name_nothing_can_slugify_is_refused(machine):
    app = machine.repo()
    result = machine.run("wt", "new", "!!!", cwd=app)
    assert result.returncode == 1
    assert "can't make a worktree name from '!!!'" in result.stderr


def test_rm_slugifies_so_a_branch_finds_its_default_worktree(machine):
    app = machine.repo()
    machine.ok("wt", "new", "feat/x", cwd=app)
    machine.ok("wt", "rm", "feat/x", cwd=app)
    assert not (machine.home / "feat-x").exists()


def test_rm_after_moving_between_branches(machine):
    app = machine.repo()
    machine.ok("wt", "new", "long", "--branch", "first", cwd=app)
    path = machine.home / "long"
    git(path, "switch", "-qc", "second")
    (path / "work").write_text("x\n")
    git(path, "add", ".")
    git(path, "commit", "-qm", "work")

    result = machine.ok("wt", "rm", "long", cwd=app)
    assert not path.exists()
    # Only the branch it was on at the end counts, and that one isn't merged.
    assert "Keeping branch second" in result.stdout
    assert git_out(app, "branch", "--list", "first", "second").split() == ["first", "second"]


def test_rm_detached_worktree(machine):
    app = machine.repo()
    machine.ok("wt", "new", "long", "--branch", "a", cwd=app)
    path = machine.home / "long"
    git(path, "checkout", "-q", "--detach")
    result = machine.ok("wt", "rm", "long", cwd=app)
    assert not path.exists()
    assert "branch" not in result.stdout.lower()


def test_rm_never_deletes_the_default_branch(machine):
    app = machine.repo()
    git(app, "switch", "-qc", "elsewhere")
    machine.ok("wt", "new", "long", "--branch", "main", cwd=app)
    machine.ok("wt", "rm", "long", cwd=app)
    assert git_out(app, "branch", "--list", "main") != ""


def test_ls(machine):
    app = machine.repo()
    (app / ".env").write_text("PORT=3000\nDATABASE_URL=postgres://localhost/app_dev?x=1\n")
    machine.ok("wt", "new", "feat/x", cwd=app)
    machine.ok("wt", "new", "gone", cwd=app)
    git(machine.home / "gone", "checkout", "-q", "--detach")

    lines = machine.ok("wt", "ls", cwd=app).stdout.splitlines()
    row = "{:<24} {:<32} {:<6} {:<32} {}"
    assert lines == [
        row.format("NAME", "BRANCH", "PORT", "DATABASE", "PATH"),
        row.format("app", "main", "3000", "app_dev", app),
        row.format("feat-x", "feat/x", "3001", "app_dev", machine.home / "feat-x"),
        row.format("gone", "(detached)", "3002", "app_dev", machine.home / "gone"),
    ]


def test_setup_order_and_failures_carry_on(machine):
    log = machine.home / "calls"
    for tool in ("mise", "pnpm", "npm", "uv"):
        machine.stub(tool, f'echo "{tool} $*" >>"{log}"')
    machine.stub("pnpm", f'echo "pnpm $*" >>"{log}"; exit 1')
    app = machine.repo()
    for name in ("mise.toml", "pnpm-lock.yaml", "package-lock.json", "uv.lock"):
        (app / name).touch()

    result = machine.ok("setup", str(app))
    assert log.read_text().splitlines() == ["mise trust", "mise install", "pnpm install", "uv sync"]
    assert "!!! failed: pnpm install (continuing)" in result.stderr


@pytest.mark.parametrize(
    "lockfile, calls",
    [
        ("package-lock.json", ["npm ci"]),
        (".tool-versions", ["mise install"]),
        ("yarn.lock", ["npx --yes yarn install"]),
    ],
)
def test_setup_by_lockfile(machine, lockfile, calls):
    log = machine.home / "calls"
    for tool in ("mise", "npm", "npx"):
        machine.stub(tool, f'echo "{tool} $*" >>"{log}"')
    app = machine.repo()
    (app / lockfile).touch()
    # Only the stubs, so a real yarn can't hide the npx fallback.
    machine.env["PATH"] = str(machine.bin)
    machine.ok("setup", str(app))
    assert log.read_text().splitlines() == calls


def test_setup_uses_yarn_when_installed(machine):
    log = machine.home / "calls"
    machine.stub("yarn", f'echo "yarn $*" >>"{log}"')
    app = machine.repo()
    (app / "yarn.lock").touch()
    machine.ok("setup", str(app))
    assert log.read_text().splitlines() == ["yarn install"]


def test_setup_defaults_to_current_directory(machine):
    log = machine.home / "calls"
    machine.stub("uv", f'pwd >>"{log}"')
    app = machine.repo()
    (app / "uv.lock").touch()
    machine.ok("setup", cwd=app)
    assert log.read_text().strip() == str(app)


def test_setup_missing_path(machine):
    result = machine.run("setup", "nowhere")
    assert result.returncode == 1


def test_usage_errors_exit_2(machine):
    assert machine.run().returncode == 2
    assert machine.run("wt").returncode == 2
    assert machine.run("wt", "new").returncode == 2
    assert machine.run("wt", "nope").returncode == 2


@pytest.mark.parametrize(
    "args",
    [
        (),
        ("wt",),
        ("wt", "new"),
        ("wt", "rm"),
        ("wt", "ls"),
        ("wt", "init"),
        ("wt", "archive"),
        ("setup",),
        ("apply",),
        ("upgrade",),
    ],
)
def test_help(machine, args):
    assert "usage: devenv" in machine.ok(*args, "--help").stdout


# ---------------------------------------------------------------- worktrees made by something else


def hand_made(machine, app, rel, branch=None):
    """A worktree made with plain git worktree add, as a UI such as Orca would."""
    path = machine.home / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    git(app, "worktree", "add", "-q", "-b", branch or path.name, str(path))
    return path


@pytest.mark.parametrize("rel", ["x", "orca-workspaces/app/x"])
def test_init_configures_a_hand_made_worktree(machine, rel):
    app = machine.repo()
    (app / ".env").write_text("SECRET=1\n")
    path = hand_made(machine, app, rel)
    result = machine.ok("wt", "init", cwd=path)
    assert env_of(path) == "PORT=3001\n"
    assert (path / ".env").is_symlink()
    assert (path / ".env").read_text() == "SECRET=1\n"
    assert f"Worktree ready: {path}" in result.stdout
    assert "  branch    x\n" in result.stdout
    listing = machine.ok("wt", "ls", cwd=app).stdout
    assert "3001" in listing and str(path) in listing


def test_init_takes_a_path(machine):
    app = machine.repo()
    path = hand_made(machine, app, "elsewhere/x")
    machine.ok("wt", "init", str(path), cwd=app)
    assert env_of(path) == "PORT=3001\n"


def test_a_second_init_changes_nothing(machine):
    app = machine.repo()
    (app / ".env").touch()
    path = hand_made(machine, app, "orca/x")
    machine.ok("wt", "init", cwd=path)
    before = env_of(path)
    # Something else listening on its port mustn't move it.
    (machine.bin / "ss-output").write_text("LISTEN 0 511 *:3001 *:*\n")
    again = machine.ok("wt", "init", cwd=path)
    assert env_of(path) == before
    assert "!!!" not in again.stderr


def test_init_refuses_the_main_checkout(machine):
    app = machine.repo()
    result = machine.run("wt", "init", cwd=app)
    assert result.returncode == 1
    assert "is the main checkout" in result.stderr
    assert not (app / ".env.local").exists()


def test_init_refuses_a_folder_that_isnt_a_worktree(machine):
    app = machine.repo()
    (app / "sub").mkdir()
    for cwd, message in [(app / "sub", "isn't the top folder"), (machine.home, "isn't in a git checkout")]:
        result = machine.run("wt", "init", str(cwd), cwd=app)
        assert result.returncode == 1
        assert message in result.stderr


def test_init_refuses_a_name_another_worktree_has(machine):
    app = machine.repo()
    first = hand_made(machine, app, "x")
    second = hand_made(machine, app, "orca/x", branch="other")
    result = machine.run("wt", "init", cwd=second)
    assert result.returncode == 1
    assert f"already named x, at {first}" in result.stderr
    assert not (second / ".env.local").exists()


def test_new_refuses_a_name_a_worktree_elsewhere_has(machine):
    app = machine.repo()
    elsewhere = hand_made(machine, app, "orca/x", branch="other")
    result = machine.run("wt", "new", "x", cwd=app)
    assert result.returncode == 1
    assert f"already named x, at {elsewhere}" in result.stderr
    assert not (machine.home / "x").exists()


def test_rm_refuses_a_name_two_worktrees_have(machine):
    app = machine.repo()
    first = hand_made(machine, app, "x")
    second = hand_made(machine, app, "orca/x", branch="other")
    result = machine.run("wt", "rm", "x", cwd=app)
    assert result.returncode == 1
    assert "more than one worktree is named x:\n" in result.stderr
    assert f"  {first}\n" in result.stderr and f"  {second}\n" in result.stderr
    assert "Give the path" in result.stderr
    assert first.is_dir() and second.is_dir()


def test_rm_by_path_removes_only_that_one(machine):
    app = machine.repo()
    first = hand_made(machine, app, "x")
    second = hand_made(machine, app, "orca/x", branch="other")
    machine.ok("wt", "rm", str(second), cwd=app)
    assert first.is_dir() and not second.exists()
    machine.ok("wt", "rm", "../x", cwd=app)
    assert not first.exists()


def test_archive_without_a_database_leaves_everything(machine):
    app = machine.repo()
    path = hand_made(machine, app, "orca/x")
    machine.ok("wt", "init", cwd=path)
    result = machine.ok("wt", "archive", cwd=path)
    assert "has no database of its own to drop" in result.stdout
    assert path.is_dir()
    assert git_out(app, "branch", "--list", "x") != ""


def test_archive_refuses_the_main_checkout(machine):
    app = machine.repo()
    result = machine.run("wt", "archive", cwd=app)
    assert result.returncode == 1
    assert "is the main checkout" in result.stderr


def test_rm_of_a_path_that_isnt_a_worktree_says_so(machine):
    app = machine.repo()
    result = machine.run("wt", "rm", "./nope", cwd=app)
    assert result.returncode == 1
    assert f"no worktree is at {app}/nope, and none is named nope" in result.stderr
