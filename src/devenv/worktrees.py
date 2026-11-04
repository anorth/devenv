"""Creating, removing and listing a checkout's worktrees."""

import os
import shutil
from pathlib import Path

from devenv import postgres, rules
from devenv.envfile import env_file_get, env_file_set
from devenv.git import Checkout, find_checkouts
from devenv.setup import setup_checkout
from devenv.system import capture, listening_ports, tailnet_name
from devenv.ui import Die, log, print_open_commands, warn


def claimed_ports(checkout: Checkout) -> set[int]:
    """Ports in the .env of every worktree of every checkout on the machine."""
    mains = {checkout.main, *find_checkouts(Path.home())}
    ports = set()
    for main in mains:
        for worktree in Checkout(main).worktrees():
            port = env_file_get(Path(worktree.path) / ".env", "PORT")
            if port.isdecimal():
                ports.add(int(port))
    return ports


def pick_port(checkout: Checkout) -> int:
    port = rules.choose_port(claimed_ports(checkout) | listening_ports())
    if port is None:
        raise Die("no free port above 3000")
    return port


def _plan_checkout(checkout: Checkout, path: Path, branch: str, base: str | None) -> tuple[str, list[str]]:
    """What to tell the user, and the `git worktree add` arguments for the branch."""
    if checkout.has_ref(f"refs/heads/{branch}"):
        plan = f"Checking out {branch}", [str(path), branch]
    elif checkout.has_ref(f"refs/remotes/origin/{branch}"):
        plan = f"Checking out {branch} from origin", ["--track", "-b", branch, str(path), f"origin/{branch}"]
    else:
        base = base or checkout.default_branch()
        if checkout.has_ref(f"refs/remotes/origin/{base}"):
            start = f"origin/{base}"
        elif checkout.has_ref(f"refs/heads/{base}"):
            start = base
        else:
            raise Die(f"base branch {base} not found locally or on origin")
        return f"Creating branch {branch} from {start}", ["--no-track", "-b", branch, str(path), start]
    if base:
        warn(f"branch {branch} already exists, so --base {base} is ignored")
    return plan


def _write_env(checkout: Checkout, path: Path, port: int, db: str | None) -> None:
    log(f"Writing .env (PORT={port})")
    env = path / ".env"
    main_env = checkout.main / ".env"
    if main_env.is_file():
        shutil.copy(main_env, env)
    else:
        warn(f"{main_env} doesn't exist; the worktree's .env has only what devenv sets")
    env_file_set(env, "PORT", str(port))
    if db:
        env_file_set(env, "DATABASE_URL", rules.database_url(db))


def _print_summary(checkout: Checkout, path: Path, branch: str, port: int, db: str | None) -> None:
    # Without a database of its own, the worktree keeps whatever DATABASE_URL the
    # main .env has, e.g. a shared external database. Say so rather than "none".
    db_note = db
    if not db:
        db_note = "(none of its own)"
        if env_file_get(path / ".env", "DATABASE_URL"):
            db_note = f"(none of its own; uses DATABASE_URL copied from {checkout.main}/.env)"
    host = tailnet_name()
    url = f"http://{host}:{port}" if host else (
        f"http://localhost:{port} (inside the VM: it isn't on the tailnet)"
    )
    print(f"\nWorktree ready: {path}")
    print(f"  branch    {branch}")
    print(f"  url       {url}")
    print(f"  database  {db_note}")
    if host:
        print_open_commands(host, path)


def new_worktree(
    checkout: Checkout, name: str, branch: str | None = None, base: str | None = None
) -> None:
    slug = rules.slugify(name)
    if not slug:
        raise Die(f"can't make a worktree name from '{name}'")
    branch = branch or name
    if capture(["git", "check-ref-format", "--branch", branch]).returncode != 0:
        raise Die(f"not a valid branch name: {branch}. Choose one with --branch.")
    path = checkout.worktree_path(slug)
    template = rules.template_db(checkout.dir)
    # Worktrees get a database only if the repo has one.
    db = rules.worktree_db(checkout.dir, slug) if postgres.db_exists(template) else None
    if db == template:
        raise Die(
            f"worktree {slug} would use the name of the template database, {template}. "
            "Pick another name, and use --branch if you want that branch."
        )

    if path.exists() or path.is_symlink():
        raise Die(f"{path} already exists. Pick another name.")
    existing = checkout.worktree_for_branch(branch)
    if existing:
        raise Die(f"branch {branch} is already checked out at {existing}")
    if db and postgres.db_exists(db):
        raise Die(f"database {db} already exists. If nothing uses it, drop it with: dropdb {db}")

    if not checkout.fetch_origin():
        warn("git fetch failed; using the refs already here")
    plan, add_args = _plan_checkout(checkout, path, branch, base)

    if db:
        log(f"Copying database {template} to {db}")
        postgres.copy_database(template, db)
    log(plan)
    if not checkout.git("worktree", "add", *add_args):
        if db and not postgres.drop(db):
            warn(f"couldn't drop {db}; drop it with: dropdb {db}")
        raise Die("git worktree add failed")

    # Chosen only now, so that the slow steps above don't leave a gap in which
    # another worktree being created can pick the same port.
    port = pick_port(checkout)
    _write_env(checkout, path, port, db)
    setup_checkout(path)
    _print_summary(checkout, path, branch, port, db)


def _uncommitted_changes(path: Path) -> list[str]:
    if not path.exists():
        return []
    result = capture(["git", "-C", str(path), "status", "--porcelain"])
    if result.returncode != 0:
        raise Die(f"can't read the state of {path}: {result.stderr.strip()}")
    # .env is ours, so it doesn't count as a change even if git doesn't ignore it.
    return [line for line in result.stdout.splitlines() if line != "?? .env"]


def _merged_into(checkout: Checkout, branch: str, target: str) -> bool:
    return any(
        checkout.has_ref(ref)
        and checkout.git_output("merge-base", "--is-ancestor", f"refs/heads/{branch}", ref).returncode == 0
        for ref in (f"refs/remotes/origin/{target}", f"refs/heads/{target}")
    )


def _delete_branch_if_merged(checkout: Checkout, branch: str) -> None:
    target = checkout.default_branch()
    if branch == target:
        return
    if not checkout.fetch_origin():
        warn("git fetch failed; checking merges against local refs")
    if _merged_into(checkout, branch, target):
        log(f"Deleting branch {branch} (merged into {target})")
        if not checkout.git("branch", "-D", branch):
            raise Die(f"couldn't delete branch {branch}")
    else:
        log(f"Keeping branch {branch}: not merged into {target}")


def remove_worktree(checkout: Checkout, name: str, force: bool = False) -> None:
    # As `wt ls` shows it, or as it was given to `wt new`: a worktree made or
    # moved by hand needn't have a slug for a name.
    worktree = checkout.worktree_named(name) or checkout.worktree_named(rules.slugify(name))
    if worktree is None:
        raise Die(f"no worktree is named {name} (devenv wt ls)")
    path = Path(worktree.path)
    if path == checkout.main:
        raise Die("refusing to remove the main checkout")
    db = rules.worktree_db(checkout.dir, path.name)
    # A worktree can have the template's name if it predates the template.
    has_db = db != rules.template_db(checkout.dir) and postgres.db_exists(db)
    # Names can collide across checkouts, so the name alone doesn't make it ours.
    env_db = rules.database_from_url(env_file_get(path / ".env", "DATABASE_URL"))
    drop_db = has_db and env_db == db

    dirty = _uncommitted_changes(path)
    if dirty and not force:
        changes = "\n".join(dirty)
        raise Die(f"{path} has uncommitted changes (use --force to remove anyway):\n{changes}")

    if drop_db:
        connections = postgres.db_connections(db)
        if connections and not force:
            raise Die(
                f"database {db} has {connections} active connection(s). "
                "Stop this worktree's dev server, or use --force."
            )

    was_inside = Path.cwd().is_relative_to(path)
    # This process may be running from inside the worktree.
    os.chdir(checkout.main)
    log(f"Removing worktree {path}")
    # Dirtiness was checked above, so force past git's own untracked-file check.
    if not checkout.git("worktree", "remove", "--force", str(path)):
        raise Die(f"git worktree remove failed for {path}")

    # Last of the two, so that a worktree git won't remove keeps its data.
    if drop_db:
        log(f"Dropping database {db}")
        if not postgres.drop(db, force=force):
            raise Die(f"couldn't drop database {db}; drop it with: dropdb {db}")
    elif has_db:
        warn(
            f"leaving database {db}: this worktree's .env doesn't use it. "
            f"If nothing else does, drop it with: dropdb {db}"
        )

    if worktree.branch:
        _delete_branch_if_merged(checkout, worktree.branch)

    if was_inside:
        print(f"\nYour shell is still in the removed worktree. Leave it with: cd {checkout.main}")


def list_worktrees(checkout: Checkout) -> None:
    row = "{:<24} {:<32} {:<6} {:<32} {}"
    print(row.format("NAME", "BRANCH", "PORT", "DATABASE", "PATH"))
    for worktree in checkout.worktrees():
        env = Path(worktree.path) / ".env"
        db = rules.database_from_url(env_file_get(env, "DATABASE_URL"))
        branch = worktree.branch or "(detached)"
        name = Path(worktree.path).name
        print(row.format(name, branch, env_file_get(env, "PORT"), db, worktree.path))
