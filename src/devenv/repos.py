"""Adding a repo to the machine as a checkout."""

import os
import shutil
import time
from pathlib import Path

from devenv import postgres, rules
from devenv.envfile import LOCAL_ENV, env_file_set, warn_if_env_not_ignored
from devenv.git import find_checkouts
from devenv.setup import setup_checkout, try_step
from devenv.system import call, tailnet_name
from devenv.ui import Die, log, print_open_commands, warn

CLONE_ATTEMPTS = 5
CLONE_RETRY_SECONDS = 3


def plan_checkout(clone_url: str, dir_arg: str | None) -> Path:
    """Where the repo's checkout goes, refusing anything that can't go there.

    This has to stay free of side effects: the host runs it, as `--check`,
    before it sets up access to the repo.
    """
    rel = rules.repo_name(clone_url) if dir_arg is None else dir_arg
    if not rules.valid_checkout_dir(rel):
        raise Die(f"invalid directory '{rel}': use <dir> or <group>/<dir> with --dir")
    home = Path.home()
    path = home / rel
    if os.path.lexists(path):
        raise Die(f"{path} already exists. Pick another with --dir.")
    if os.path.lexists(path.parent) and not path.parent.is_dir():
        raise Die(f"{path.parent} isn't a folder. Pick another with --dir.")
    # Home itself may well be a repo, of dotfiles say; only a group can't be.
    if path.parent != home and os.path.lexists(path.parent / ".git"):
        raise Die(f"{path.parent} is a repo, so it can't hold a checkout. Pick another with --dir.")
    # Compare what's actually shared: my-app and my_app differ, but not in Postgres.
    default_db = rules.default_db(path.name)
    for checkout in find_checkouts(home):
        if rules.default_db(checkout.name) == default_db:
            raise Die(
                f"the checkout at {checkout} would share databases with {path}. "
                "Pick another name with --dir."
            )
    return path


def _clone(clone_url: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    log(f"Cloning {clone_url} to {path}")
    # New access, a deploy key especially, can take a moment to work.
    for attempt in range(1, CLONE_ATTEMPTS + 1):
        if call(["git", "clone", "--", clone_url, str(path)]):
            return
        if attempt < CLONE_ATTEMPTS:
            warn(f"clone failed (attempt {attempt} of {CLONE_ATTEMPTS}); retrying in {CLONE_RETRY_SECONDS}s")
            time.sleep(CLONE_RETRY_SECONDS)
    raise Die("git clone failed. Check the repo access this machine was given.")


def _copy_env_example(path: Path) -> None:
    example = path / ".env.example"
    env = path / ".env"
    if not example.is_file() or os.path.lexists(env):
        return
    log("Copying .env.example to .env")
    try:
        shutil.copy(example, env)
    except OSError as err:
        warn(f"can't copy .env.example to .env: {err} (continuing)")


def _write_database_env(path: Path, db: str) -> None:
    settings = postgres.connection_env(db)
    env = path / LOCAL_ENV
    if os.path.lexists(env):
        lines = "\n".join(f"  {key}={value}" for key, value in settings.items())
        warn(f"{env} already exists, so add the database settings to it yourself:\n{lines}")
        return
    log(f"Writing the database settings to {LOCAL_ENV}")
    for key, value in settings.items():
        env_file_set(env, key, value)
    warn_if_env_not_ignored(path)


def _run_setup_script(path: Path) -> None:
    script = path / "setup.sh"
    if script.is_file():
        try_step(["./setup.sh"] if os.access(script, os.X_OK) else ["bash", "./setup.sh"], path)


def _print_summary(path: Path, db: str | None, ran_setup: bool) -> None:
    print(f"\nCheckout ready: {path}")
    host = tailnet_name()
    if host:
        print_open_commands(host, path)
    if db:
        print(f"\nDatabase: {db}")
    if not ran_setup:
        print(f"\nSetup was skipped. To finish it: devenv setup {path}, then ./setup.sh if it has one.")
    print(f"\nWorktrees: cd {path} && devenv wt new <name>")


def add_repo(clone_url: str, path: Path, *, create_db: bool, run_setup: bool) -> None:
    """Clone a repo to a path from plan_checkout, and set it up."""
    _clone(clone_url, path)

    db = rules.default_db(path.name) if create_db else None
    if db:
        log(f"Creating database {db}")
        if not postgres.create(db):
            warn(f"createdb {db} failed; devenv wt new needs it as a template (continuing)")

    log(f"Setting up {path}")
    _copy_env_example(path)
    if db:
        _write_database_env(path, db)
    if run_setup:
        setup_checkout(path)
        _run_setup_script(path)
    _print_summary(path, db, run_setup)
