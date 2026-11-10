"""Creating, removing and listing a checkout's worktrees."""

import os
from pathlib import Path

from devenv import postgres, rules
from devenv.envfile import (
    LOCAL_ENV,
    SHARED_ENV,
    checkout_databases,
    checkout_env_get,
    checkout_named_database,
    env_file_get,
    env_file_set,
    warn_if_env_not_ignored,
)
from devenv.git import Checkout, current_checkout, find_checkouts, same_path
from devenv.setup import setup_checkout
from devenv.system import capture, listening_ports, tailnet_name
from devenv.ui import Die, log, print_open_commands, warn


def claimed_ports(checkout: Checkout) -> set[int]:
    """The port of every worktree of every checkout on the machine."""
    mains = {checkout.main, *find_checkouts(Path.home())}
    ports = set()
    for main in mains:
        for worktree in Checkout(main).worktrees():
            port = checkout_env_get(Path(worktree.path), "PORT")
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


def _link_shared_env(checkout: Checkout, path: Path) -> None:
    link = path / SHARED_ENV
    main_env = checkout.main / SHARED_ENV
    if link.is_symlink() and same_path(link, main_env):
        return  # Linked already, by an earlier init.
    if os.path.lexists(link):
        warn(f"the repo has its own {SHARED_ENV}, so this worktree doesn't share {main_env}")
    elif main_env.is_file():
        # Relative, so that a folder of checkouts can be moved.
        link.symlink_to(os.path.relpath(main_env, path))
    else:
        warn(f"{main_env} doesn't exist; the worktree has only what's in its {LOCAL_ENV}")


def _write_env(checkout: Checkout, path: Path, port: int, db: str | None) -> None:
    log(f"Writing {LOCAL_ENV} (PORT={port})")
    _link_shared_env(checkout, path)
    env = path / LOCAL_ENV
    env_file_set(env, "PORT", str(port))
    if db:
        for key, value in postgres.connection_env(db).items():
            env_file_set(env, key, value)
    warn_if_env_not_ignored(path)


def _print_summary(checkout: Checkout, path: Path, branch: str, port: int, db: str | None) -> None:
    # Without a database of its own, the worktree gets whatever settings the
    # shared file has, e.g. a shared external database. Say so rather than "none".
    db_note = db
    if not db:
        db_note = "(none of its own)"
        if checkout_databases(path):
            db_note = f"(none of its own; uses the settings in its {SHARED_ENV})"
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


def _find_template(checkout: Checkout) -> str | None:
    """The database for a new worktree to copy, if there's one to copy."""
    try:
        named = checkout_named_database(checkout.main)
    except rules.DatabaseConflict as conflict:
        raise Die(f"{checkout.main}'s env files disagree: {conflict}. Make them agree.") from None
    if named is None:
        return None
    if not named.local:
        warn(
            f"{checkout.main}'s env files name database {named.name}, "
            "which isn't on the local Postgres, so it won't be copied"
        )
        return None
    if not postgres.db_exists(named.name):
        warn(f"{checkout.main}'s env files name database {named.name}, which doesn't exist")
        return None
    return named.name


def _plan_database(checkout: Checkout, name: str) -> tuple[str | None, str | None]:
    """The template to copy and the worktree's database, or Nones if the repo has none."""
    template = _find_template(checkout)
    # Worktrees get a database only if the repo has one.
    db = rules.worktree_db(checkout.dir, name) if template else None
    if template and db == template:
        raise Die(
            f"worktree {name} would use the name of the template database, {template}. "
            "Pick another name, and use --branch if you want that branch."
        )
    return template, db


def _database_exists_error(db: str) -> Die:
    return Die(f"database {db} already exists. If nothing uses it, drop it with: dropdb {db}")


def _refuse_taken_name(checkout: Checkout, name: str, path: Path) -> None:
    """Names are unique among a checkout's worktrees: databases and wt rm go by them."""
    others = [w.path for w in checkout.worktrees_named(name) if not same_path(w.path, path)]
    if others:
        raise Die(
            f"another worktree of {checkout.main} is already named {name}, at {others[0]}. "
            f"Names must be unique within a checkout, so pick another name for {path}."
        )


def _configure(checkout: Checkout, path: Path, branch: str, db: str | None) -> None:
    """Everything a worktree gets once it exists and its database is in place.

    A PORT it already has is kept, so this can be run again.
    """
    # Chosen only now, so that the slow steps before don't leave a gap in which
    # another worktree being created can pick the same port.
    existing = env_file_get(path / LOCAL_ENV, "PORT")
    port = int(existing) if existing.isdecimal() else pick_port(checkout)
    _write_env(checkout, path, port, db)
    setup_checkout(path)
    _print_summary(checkout, path, branch, port, db)


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
    if path.exists() or path.is_symlink():
        raise Die(f"{path} already exists. Pick another name.")
    _refuse_taken_name(checkout, slug, path)
    existing = checkout.worktree_for_branch(branch)
    if existing:
        raise Die(f"branch {branch} is already checked out at {existing}")
    template, db = _plan_database(checkout, slug)
    if db and postgres.db_exists(db):
        raise _database_exists_error(db)

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
    _configure(checkout, path, branch, db)


def _existing_worktree(path: Path) -> tuple[Checkout, rules.Worktree]:
    """The checkout and worktree whose top folder is `path`, refusing the main checkout."""
    path = path.absolute()
    if not path.is_dir():
        raise Die(f"{path} isn't a directory")
    checkout = current_checkout(path)
    worktree = checkout.worktree_at(path)
    if worktree is None:
        raise Die(f"{path} isn't the top folder of one of {checkout.main}'s worktrees")
    if same_path(path, checkout.main):
        raise Die(f"{path} is the main checkout; init and archive are for its worktrees")
    return checkout, worktree


def init_worktree(path: Path) -> None:
    """Give a worktree that something else created what wt new would have.

    Running it again changes nothing that's already in place.
    """
    checkout, worktree = _existing_worktree(path)
    path = Path(worktree.path)
    _refuse_taken_name(checkout, path.name, path)
    template, db = _plan_database(checkout, path.name)
    if db:
        own = rules.databases_named(
            env_file_get(path / LOCAL_ENV, "DATABASE_URL"), env_file_get(path / LOCAL_ENV, "DB_DATABASE")
        )
        exists = postgres.db_exists(db)
        if exists and db not in own:
            raise _database_exists_error(db)
        if not exists:
            log(f"Copying database {template} to {db}")
            postgres.copy_database(template, db)
            # Recorded at once, so that a run that dies before the rest of the env
            # is written can be run again rather than refusing its own copy.
            for key, value in postgres.connection_env(db).items():
                env_file_set(path / LOCAL_ENV, key, value)
    _configure(checkout, path, worktree.branch or "(detached)", db)


def _own_database(checkout: Checkout, path: Path) -> tuple[str | None, str | None]:
    """The worktree's database if it's safe to drop, and why one is kept if it isn't."""
    db = rules.worktree_db(checkout.dir, path.name)
    # A worktree can have the template's name if it predates the template. Either
    # setting protects a database, because they may disagree.
    if db in checkout_databases(checkout.main) or not postgres.db_exists(db):
        return None, None
    # Names can collide across checkouts, so the name alone doesn't make it ours.
    named = checkout_databases(path)
    if named == {db}:
        return db, None
    return None, (
        f"leaving database {db}: this worktree's env files name {', '.join(sorted(named)) or 'none'}. "
        f"If nothing else does, drop it with: dropdb {db}"
    )


def _drop_database(db: str, force: bool) -> None:
    """Drop a worktree's database; with force, closing any connections to it."""
    connections = postgres.db_connections(db) if force else 0
    closing = f", closing its {connections} connection(s)" if connections else ""
    log(f"Dropping database {db}{closing}")
    if not postgres.drop(db, force=force):
        hint = "dropdb --force" if force else "dropdb"
        raise Die(f"couldn't drop database {db}; drop it with: {hint} {db}")


def archive_worktree(path: Path) -> None:
    """Drop a worktree's database, leaving the worktree for something else to remove.

    A database in use is dropped anyway: the user has already chosen to delete
    the worktree, and a database left behind would go unnoticed.
    """
    checkout, worktree = _existing_worktree(path)
    db, kept = _own_database(checkout, Path(worktree.path))
    if kept:
        warn(kept)
        return
    if not db:
        log(f"{worktree.path} has no database of its own to drop")
        return
    _drop_database(db, force=True)


def _uncommitted_changes(path: Path) -> list[str]:
    if not path.exists():
        return []
    result = capture(["git", "-C", str(path), "status", "--porcelain"])
    if result.returncode != 0:
        raise Die(f"can't read the state of {path}: {result.stderr.strip()}")
    # These are ours, so they don't count as changes even if git doesn't ignore them.
    ours = {f"?? {SHARED_ENV}", f"?? {LOCAL_ENV}"}
    return [line for line in result.stdout.splitlines() if line not in ours]


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


def _find_for_removal(checkout: Checkout, name_or_path: str) -> rules.Worktree:
    """The one worktree an argument means, by path or by name, refusing to guess."""
    # A path has a slash. A name with one, such as a branch, falls back to its slug.
    if "/" in name_or_path:
        worktree = checkout.worktree_at(Path(name_or_path))
        if worktree:
            return worktree
    # As `wt ls` shows it, or as it was given to `wt new`: a worktree made or
    # moved by hand needn't have a slug for a name.
    matches = checkout.worktrees_named(name_or_path) or checkout.worktrees_named(rules.slugify(name_or_path))
    if not matches:
        if "/" in name_or_path:
            raise Die(
                f"no worktree is at {Path(name_or_path).absolute()}, and none is named "
                f"{rules.slugify(name_or_path)} (devenv wt ls)"
            )
        raise Die(f"no worktree is named {name_or_path} (devenv wt ls)")
    if len(matches) > 1:
        paths = "\n".join(f"  {w.path}" for w in matches)
        raise Die(
            f"more than one worktree is named {name_or_path}:\n{paths}\n"
            "Give the path of the one to remove, e.g. devenv wt rm <path>"
        )
    return matches[0]


def remove_worktree(checkout: Checkout, name_or_path: str, force: bool = False) -> None:
    worktree = _find_for_removal(checkout, name_or_path)
    path = Path(worktree.path)
    if path == checkout.main:
        raise Die("refusing to remove the main checkout")
    db, kept = _own_database(checkout, path)

    dirty = _uncommitted_changes(path)
    if dirty and not force:
        changes = "\n".join(dirty)
        raise Die(f"{path} has uncommitted changes (use --force to remove anyway):\n{changes}")

    if db:
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
    if db:
        _drop_database(db, force)
    elif kept:
        warn(kept)

    if worktree.branch:
        _delete_branch_if_merged(checkout, worktree.branch)

    if was_inside:
        print(f"\nYour shell is still in the removed worktree. Leave it with: cd {checkout.main}")


def list_worktrees(checkout: Checkout) -> None:
    row = "{:<24} {:<32} {:<6} {:<32} {}"
    print(row.format("NAME", "BRANCH", "PORT", "DATABASE", "PATH"))
    for worktree in checkout.worktrees():
        wt_path = Path(worktree.path)
        db = ",".join(sorted(checkout_databases(wt_path)))
        branch = worktree.branch or "(detached)"
        print(row.format(wt_path.name, branch, checkout_env_get(wt_path, "PORT"), db, worktree.path))
