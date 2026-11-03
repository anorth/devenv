import json
import re
import shutil
import subprocess

import pytest

from conftest import git, git_out


def env_of(path):
    return (path / ".env").read_text()


def test_new_branch_from_default(machine):
    app = machine.repo(origin=True)
    result = machine.ok("wt", "new", "feat/x", cwd=app)
    path = machine.home / "app-wt" / "feat-x"
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
    assert git_out(machine.home / "app-wt/topic", "branch", "--show-current") == "topic"


def test_new_from_origin_only_branch(machine):
    app = machine.repo(origin=True)
    seed = machine.home.parent / "seeds/app"
    git(seed, "checkout", "-qb", "remote-only")
    (seed / "extra").write_text("x\n")
    git(seed, "add", ".")
    git(seed, "commit", "-qm", "extra")
    git(seed, "push", "-q", "origin", "remote-only")

    machine.ok("wt", "new", "remote-only", cwd=app)
    path = machine.home / "app-wt/remote-only"
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
    assert (machine.home / "app-wt/feat/dev-only").exists()


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
    assert not (machine.home / "app-wt/x").exists()


def test_new_from_inside_a_worktree(machine):
    app = machine.repo()
    machine.ok("wt", "new", "one", cwd=app)
    subdir = machine.home / "app-wt/one/sub"
    subdir.mkdir()
    machine.ok("wt", "new", "two", cwd=subdir)
    path = machine.home / "app-wt/two"
    assert "PORT=3002\n" in env_of(path)


def test_nested_checkout(machine):
    app = machine.repo("group/repo")
    machine.ok("wt", "new", "feat/x", cwd=app)
    path = machine.home / "group/repo-wt/feat-x"
    assert (path / "README").exists()
    assert not (machine.home / "repo-wt").exists()


def test_port_skips_one_claimed_by_another_checkout(machine):
    first = machine.repo("first")
    nested = machine.repo("group/second")
    machine.ok("wt", "new", "a", cwd=first)
    machine.ok("wt", "new", "b", cwd=nested)
    assert "PORT=3002\n" in env_of(machine.home / "group/second-wt/b")


def test_port_claimed_by_main_checkout_env(machine):
    app = machine.repo()
    (app / ".env").write_text("export PORT='3001'\n")
    machine.ok("wt", "new", "a", cwd=app)
    assert "PORT=3002\n" in env_of(machine.home / "app-wt/a")


def test_port_skips_listening_socket(machine):
    (machine.bin / "ss-output").write_text("LISTEN 0 511 *:3001 *:*\n")
    app = machine.repo()
    machine.ok("wt", "new", "a", cwd=app)
    assert "PORT=3002\n" in env_of(machine.home / "app-wt/a")


def test_env_is_copied_and_overridden(machine):
    app = machine.repo()
    (app / ".env").write_text("SECRET=keep\nPORT=9\nDATABASE_URL=postgres://elsewhere/shared\n")
    result = machine.ok("wt", "new", "a", cwd=app)
    env = env_of(machine.home / "app-wt/a")
    assert "SECRET=keep\n" in env
    assert "DATABASE_URL=postgres://elsewhere/shared\n" in env
    assert f"uses DATABASE_URL copied from {app}/.env" in result.stdout


def test_missing_main_env_warns(machine):
    app = machine.repo()
    result = machine.ok("wt", "new", "a", cwd=app)
    assert f"!!! {app}/.env doesn't exist" in result.stderr


def test_tailnet_summary(machine):
    status = json.dumps({"BackendState": "Running", "Self": {"DNSName": "box.tail.ts.net."}})
    machine.stub("tailscale", f"echo '{status}'")
    app = machine.repo()
    result = machine.ok("wt", "new", "a", cwd=app)
    path = machine.home / "app-wt/a"
    assert "http://box:3001\n" in result.stdout
    assert "Open it:" in result.stdout
    assert re.search(rf"cursor --remote ssh-remote\+\S+@box {path}\n", result.stdout)
    assert re.search(rf"zed ssh://\S+@box{path}\n", result.stdout)


def test_refuses_existing_path(machine):
    app = machine.repo()
    (machine.home / "app-wt/a").mkdir(parents=True)
    result = machine.run("wt", "new", "a", cwd=app)
    assert result.returncode == 1
    assert "already exists (devenv wt ls shows worktrees)" in result.stderr


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


def test_dies_when_postgres_cant_be_queried(machine):
    machine.stub("psql", "cat >/dev/null; exit 2")
    app = machine.repo()
    result = machine.run("wt", "new", "a", cwd=app)
    assert result.returncode == 1
    assert "can't query Postgres; is it running?" in result.stderr
    assert not (machine.home / "app-wt/a").exists()


def test_filesystem_errors_are_reported_without_a_traceback(machine):
    app = machine.repo()
    (machine.home / "app-wt").write_text("in the way\n")
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
    path = machine.home / "app-wt/a"
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
    assert not (machine.home / "app-wt/a").exists()


def test_rm_keeps_unmerged_branch(machine):
    app = machine.repo()
    machine.ok("wt", "new", "a", cwd=app)
    path = machine.home / "app-wt/a"
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
    path = machine.home / "app-wt/a"
    (path / "work").write_text("x\n")
    git(path, "add", "work")
    git(path, "commit", "-qm", "work")
    git(path, "push", "-q", "origin", "HEAD:main")

    result = machine.ok("wt", "rm", "a", cwd=app)
    assert "Deleting branch a (merged into main)" in result.stdout


def test_rm_from_inside_the_worktree(machine):
    app = machine.repo()
    machine.ok("wt", "new", "a", cwd=app)
    path = machine.home / "app-wt/a"
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
    shutil.rmtree(machine.home / "app-wt/a")
    machine.ok("wt", "rm", "a", cwd=app)
    assert "app-wt" not in git_out(app, "worktree", "list")


def test_rm_refuses_main_checkout(machine):
    app = machine.repo()
    result = machine.run("wt", "rm", "main", cwd=app)
    assert result.returncode == 1
    assert "devenv: refusing to remove the main checkout\n" in result.stderr


def test_rm_unknown_branch(machine):
    app = machine.repo()
    result = machine.run("wt", "rm", "nope", cwd=app)
    assert result.returncode == 1
    assert "no worktree has branch nope checked out (devenv wt ls)" in result.stderr


def test_ls(machine):
    app = machine.repo()
    (app / ".env").write_text("PORT=3000\nDATABASE_URL=postgres://localhost/app_dev?x=1\n")
    machine.ok("wt", "new", "feat/x", cwd=app)
    machine.ok("wt", "new", "gone", cwd=app)
    git(machine.home / "app-wt/gone", "checkout", "-q", "--detach")

    lines = machine.ok("wt", "ls", cwd=app).stdout.splitlines()
    row = "{:<32} {:<6} {:<32} {}"
    assert lines == [
        row.format("BRANCH", "PORT", "DATABASE", "PATH"),
        row.format("main", "3000", "app_dev", app),
        row.format("feat/x", "3001", "app_dev", machine.home / "app-wt/feat-x"),
        row.format("(detached)", "3002", "app_dev", machine.home / "app-wt/gone"),
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
    "args", [(), ("wt",), ("wt", "new"), ("wt", "rm"), ("wt", "ls"), ("setup",)]
)
def test_help(machine, args):
    assert "usage: devenv" in machine.ok(*args, "--help").stdout
