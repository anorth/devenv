import re
from pathlib import Path

import pytest

from conftest import git, git_out
from devenv import postgres, repos
from devenv.ui import Die


def make_script_executable(machine):
    seed = machine.home.parent / "seeds/app"
    (seed / "setup.sh").chmod(0o755)
    git(seed, "commit", "-qam", "exec")
    git(seed, "push", "-q", "origin", "main")


def test_clones_to_the_repo_name(machine):
    result = machine.ok("repo", "add", machine.origin("app"))
    path = machine.home / "app"
    assert (path / "README").exists()
    assert f"Checkout ready: {path}" in result.stdout
    assert f"cd {path} && devenv wt new <name>" in result.stdout
    for absent in ("Open it:", "DATABASE_URL", "Setup was skipped"):
        assert absent not in result.stdout


def test_nested_dir_creates_the_group_folder(machine):
    machine.ok("repo", "add", machine.origin("app"), "--dir", "group/x")
    assert git_out(machine.home / "group/x", "rev-parse", "--abbrev-ref", "HEAD") == "main"


@pytest.mark.parametrize("bad", ["a/b/c", "../x", ".hidden", ""])
def test_refuses_an_invalid_dir(machine, bad):
    result = machine.run("repo", "add", machine.origin("app"), "--dir", bad)
    assert result.returncode == 1
    assert f"invalid directory '{bad}'" in result.stderr


def test_refuses_a_url_with_no_usable_name(machine):
    result = machine.run("repo", "add", "https://github.com/o/.git")
    assert result.returncode == 1
    assert "use <dir> or <group>/<dir> with --dir" in result.stderr


def test_refuses_an_existing_path(machine):
    (machine.home / "app").mkdir()
    url = machine.origin("app")
    result = machine.run("repo", "add", url)
    assert result.returncode == 1
    assert f"{machine.home / 'app'} already exists" in result.stderr


def test_refuses_a_dangling_symlink(machine):
    (machine.home / "app").symlink_to(machine.home / "nowhere")
    assert machine.run("repo", "add", machine.origin("app")).returncode == 1


def test_check_refuses_a_group_that_is_not_a_folder(machine):
    (machine.home / "group").write_text("")
    result = machine.run("repo", "add", "https://github.com/o/x", "--dir", "group/x", "--check")
    assert result.returncode == 1
    assert f"{machine.home / 'group'} isn't a folder" in result.stderr


def test_check_refuses_a_group_that_is_a_repo(machine):
    existing = machine.repo("app")
    result = machine.run("repo", "add", "https://github.com/o/x", "--dir", "app/x", "--check")
    assert result.returncode == 1
    assert f"{existing} is a repo" in result.stderr


def test_home_can_be_a_repo(machine):
    git(machine.home, "init", "-q")
    machine.ok("repo", "add", machine.origin("app"))


def test_refuses_a_clash_with_a_nested_checkout_naming_it(machine):
    existing = machine.repo("other/x")
    result = machine.run("repo", "add", machine.origin("x"))
    assert result.returncode == 1
    assert str(existing) in result.stderr
    assert "share databases" in result.stderr
    assert not (machine.home / "x").exists()


def test_refuses_a_nested_dir_clashing_with_a_top_level_checkout(machine):
    existing = machine.repo("x")
    result = machine.run("repo", "add", machine.origin("y"), "--dir", "group/x")
    assert result.returncode == 1
    assert str(existing) in result.stderr
    assert not (machine.home / "group").exists()


def test_refuses_a_different_name_with_the_same_databases(machine):
    existing = machine.repo("my-app")
    result = machine.run("repo", "add", machine.origin("my_app"))
    assert result.returncode == 1
    assert str(existing) in result.stderr


def test_check_refuses_without_cloning(machine):
    machine.repo("other/x")
    url = machine.origin("x")
    assert machine.run("repo", "add", url, "--check").returncode == 1
    assert not (machine.home / "x").exists()


def test_check_passes_without_cloning_or_creating_folders(machine):
    # The URL needn't work yet: the host checks before it sets up access.
    result = machine.ok("repo", "add", "https://github.com/o/x.git", "--dir", "group/x", "--check")
    assert f"can be added at {machine.home / 'group/x'}" in result.stdout
    assert not (machine.home / "group").exists()


def test_copies_env_example(machine):
    machine.ok("repo", "add", machine.origin("app", {".env.example": "A=1\n"}))
    assert (machine.home / "app/.env").read_text() == "A=1\n"


def test_copies_env_example_even_with_no_setup(machine):
    machine.ok("repo", "add", machine.origin("app", {".env.example": "A=1\n"}), "--no-setup")
    assert (machine.home / "app/.env").exists()


def test_keeps_a_committed_env(machine):
    machine.ok("repo", "add", machine.origin("app", {".env.example": "A=1\n", ".env": "B=2\n"}))
    assert (machine.home / "app/.env").read_text() == "B=2\n"


def test_setup_runs_devenv_setup_then_the_script(machine):
    machine.stub("mise", f'echo "mise $*" >>"{machine.home}/ran"')
    url = machine.origin("app", {"mise.toml": "", "setup.sh": f'echo script >>"{machine.home}/ran"\n'})
    machine.ok("repo", "add", url)
    # setup.sh isn't executable here, so it's run with bash.
    assert (machine.home / "ran").read_text() == "mise trust\nmise install\nscript\n"


def test_setup_runs_an_executable_script_directly(machine):
    url = machine.origin("app", {"setup.sh": f'#!/bin/sh\necho "$0" >"{machine.home}/ran"\n'})
    make_script_executable(machine)
    machine.ok("repo", "add", url)
    assert (machine.home / "ran").read_text() == "./setup.sh\n"


def test_no_setup_skips_setup_and_says_so(machine):
    url = machine.origin("app", {"setup.sh": f'touch "{machine.home}/ran"\n'})
    result = machine.ok("repo", "add", url, "--no-setup")
    assert not (machine.home / "ran").exists()
    assert "Setup was skipped" in result.stdout


def test_failing_setup_warns_and_carries_on(machine):
    machine.stub("mise", "exit 1")
    url = machine.origin("app", {"mise.toml": "", "setup.sh": f'touch "{machine.home}/ran"; exit 1\n'})
    result = machine.ok("repo", "add", url)
    assert "!!! failed: mise install" in result.stderr
    assert "!!! failed: bash ./setup.sh" in result.stderr
    assert (machine.home / "ran").exists()
    assert "Checkout ready" in result.stdout


def test_executable_script_without_a_shebang_only_warns(machine):
    url = machine.origin("app", {"setup.sh": "true\n"})
    make_script_executable(machine)
    result = machine.ok("repo", "add", url)
    assert "!!! failed: ./setup.sh" in result.stderr


def test_db_creates_the_template(machine):
    machine.stub("createdb", f'echo "$*" >"{machine.home}/created"')
    result = machine.ok("repo", "add", machine.origin("My-App"), "--db")
    assert (machine.home / "created").read_text() == "my_app_dev\n"
    assert "Database: my_app_dev\n" in result.stdout
    env = (machine.home / "My-App/.env.local").read_text()
    assert "DATABASE_URL=postgres://localhost/my_app_dev\n" in env
    assert "DB_DATABASE=my_app_dev\n" in env
    assert "DB_PASSWORD=\n" in env
    assert "PORT=" not in env.replace("DB_PORT=", "")


def test_db_leaves_an_existing_local_env_and_shows_the_settings(machine):
    machine.stub("createdb", "exit 0")
    result = machine.ok("repo", "add", machine.origin("app", {".env.local": "MINE=1\n"}), "--db")
    assert (machine.home / "app/.env.local").read_text() == "MINE=1\n"
    assert ".env.local already exists" in result.stderr
    assert "  DB_DATABASE=app_dev\n" in result.stderr


def test_db_warns_when_env_files_are_not_ignored(machine):
    machine.stub("createdb", "exit 0")
    (machine.home / ".gitignore_global").write_text("")
    result = machine.ok("repo", "add", machine.origin("app"), "--db")
    assert "!!! .env.local isn't gitignored" in result.stderr


def test_no_db_writes_no_local_env(machine):
    machine.ok("repo", "add", machine.origin("app"))
    assert not (machine.home / "app/.env.local").exists()


def test_db_failure_only_warns(machine):
    machine.stub("createdb", "exit 1")
    result = machine.ok("repo", "add", machine.origin("app"), "--db")
    assert "!!! createdb app_dev failed; devenv wt new needs it as a template" in result.stderr


def test_open_commands_on_the_tailnet(machine):
    machine.stub(
        "tailscale",
        """echo '{"BackendState": "Running", "Self": {"DNSName": "box.tail.ts.net."}}'""",
    )
    path = machine.home / "app"
    result = machine.ok("repo", "add", machine.origin("app"))
    assert re.search(rf"cursor --remote ssh-remote\+\S+@box {path}$", result.stdout, re.M)
    assert re.search(rf"zed ssh://\S+@box{path}$", result.stdout, re.M)


def test_clone_gives_up_after_five_attempts(machine, monkeypatch, capfd):
    slept = []
    monkeypatch.setattr(repos.time, "sleep", slept.append)
    path = machine.home / "group/x"
    with pytest.raises(Die, match="Check the repo access"):
        repos.add_repo(f"file://{machine.home.parent}/missing.git", path, create_db=False, run_setup=True)
    assert slept == [3, 3, 3, 3]
    warnings = capfd.readouterr().err
    assert "attempt 1 of 5" in warnings and "attempt 4 of 5" in warnings
    assert "attempt 5 of 5" not in warnings


def test_clone_retries_until_access_works(machine, monkeypatch):
    origin = machine.home.parent / "origins/app.git"
    url = f"file://{origin}"
    # The repo appears during the first wait, as a new deploy key takes effect.
    monkeypatch.setattr(repos.time, "sleep", lambda _: machine.origin("app"))
    repos.add_repo(url, machine.home / "app", create_db=False, run_setup=True)
    assert (machine.home / "app/README").exists()


def test_with_a_real_template(pg_machine):
    machine = pg_machine
    url = machine.origin("devenv_repo_test")
    db = "devenv_repo_test_dev"
    try:
        machine.ok("repo", "add", url, "--dir", "group/devenv_repo_test", "--db")
        assert postgres.db_exists(db)
        checkout = machine.home / "group/devenv_repo_test"
        machine.ok("wt", "new", "feat", cwd=checkout)
        assert "devenv_repo_test_feat" in ((checkout.parent / "feat/.env.local").read_text())
    finally:
        for name in (db, "devenv_repo_test_feat"):
            postgres.drop(name)
