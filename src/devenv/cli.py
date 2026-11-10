"""The `devenv` command line."""

import argparse
from pathlib import Path

from devenv.apply import apply, upgrade
from devenv.git import current_checkout
from devenv.repos import add_repo, plan_checkout
from devenv.setup import setup_checkout
from devenv.ui import guarded, log
from devenv.worktrees import (
    archive_worktree,
    init_worktree,
    list_worktrees,
    new_worktree,
    remove_worktree,
)

DESCRIPTION = """\
Manage checkouts, worktrees and the user-level environment of a dev machine.
It keeps no state of its own: git's worktree list, the env files, Postgres and
its naming rules say it all.
"""

WT_DESCRIPTION = """\
Manage the worktrees of the repo you're in, from its main checkout or any
worktree. wt new puts a worktree next to the main checkout; one that something
else made can be anywhere, and wt init sets it up. A worktree's name is its
folder's, unique within the repo, and it keeps its name, port and database when
it changes branch, which is plain git switch.

Its .env links to the main checkout's, and its own .env.local holds its PORT. If
the main checkout's env files name a local database, by DATABASE_URL or
DB_DATABASE, the worktree gets its own copy, with the settings in .env.local.
"""


def _wt_new(args: argparse.Namespace) -> None:
    new_worktree(current_checkout(), args.name, branch=args.branch, base=args.base)


def _wt_rm(args: argparse.Namespace) -> None:
    remove_worktree(current_checkout(), args.name, args.force)


def _wt_init(args: argparse.Namespace) -> None:
    init_worktree(Path(args.path))


def _wt_archive(args: argparse.Namespace) -> None:
    archive_worktree(Path(args.path))


def _wt_ls(args: argparse.Namespace) -> None:
    list_worktrees(current_checkout())


def _setup(args: argparse.Namespace) -> None:
    setup_checkout(Path(args.path))


def _repo_add(args: argparse.Namespace) -> None:
    path = plan_checkout(args.clone_url, args.dir)
    if args.check:
        log(f"This repo can be added at {path}")
    else:
        add_repo(args.clone_url, path, create_db=args.db, run_setup=args.setup)


def _apply(args: argparse.Namespace) -> None:
    apply()


def _upgrade(args: argparse.Namespace) -> int:
    return upgrade()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="devenv", description=DESCRIPTION, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    commands = parser.add_subparsers(metavar="<command>", required=True)

    wt = commands.add_parser(
        "wt",
        help="create, remove or list worktrees",
        description=WT_DESCRIPTION,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    wt_commands = wt.add_subparsers(metavar="<command>", required=True)

    new = wt_commands.add_parser(
        "new",
        help="create a worktree with its own port and database",
        description="Create a worktree called <name>, with its own port and, "
        "if the main checkout's env files name a local database, its own copy of it.",
    )
    new.add_argument(
        "name",
        metavar="<name>",
        help="the worktree's name, slugified, which is its directory and the end of "
        "its database's name",
    )
    new.add_argument(
        "--branch",
        metavar="<branch>",
        help="branch to work on: checked out if it exists locally or on origin, "
        "otherwise created from --base (default: <name>)",
    )
    new.add_argument(
        "--base",
        metavar="<branch>",
        help="start a new branch from this (default: origin's default branch, usually main)",
    )
    new.set_defaults(run=_wt_new)

    rm = wt_commands.add_parser(
        "rm",
        help="remove a worktree and its database",
        description="Remove a worktree and drop its database, if it has one. "
        "Its current branch is deleted too if it's merged into the default branch.",
    )
    rm.add_argument(
        "name",
        metavar="<name>|<path>",
        help="the worktree's name, as devenv wt ls shows it, or its path (with a /, e.g. ./x). "
        "A name more than one worktree has is refused: give the path",
    )
    rm.add_argument(
        "--force",
        action="store_true",
        help="remove even with uncommitted changes, and drop the database even while it's in use",
    )
    rm.set_defaults(run=_wt_rm)

    init = wt_commands.add_parser(
        "init",
        help="give an existing worktree its own port and database",
        description="Give a worktree that something else created, such as git worktree add or "
        "a worktree UI's setup script, what devenv wt new would have: its own port and, if the "
        "main checkout's env files name a local database, its own copy of it, then its tools "
        "and dependencies. Running it again keeps what's already in place.",
    )
    init.add_argument(
        "path", metavar="<path>", nargs="?", default=".", help="the worktree's top folder (default: .)"
    )
    init.set_defaults(run=_wt_init)

    archive = wt_commands.add_parser(
        "archive",
        help="drop a worktree's database, leaving the worktree",
        description="Drop a worktree's database, by the rules devenv wt rm uses, and leave the "
        "worktree and its branch for something else to remove, such as a worktree UI's archive "
        "script. A database in use is dropped anyway, closing its connections.",
    )
    archive.add_argument(
        "path", metavar="<path>", nargs="?", default=".", help="the worktree's top folder (default: .)"
    )
    archive.set_defaults(run=_wt_archive)

    ls = wt_commands.add_parser(
        "ls",
        help="show worktrees with their branch, port and database",
        description="Show the repo's worktrees with their branch, port and database.",
    )
    ls.set_defaults(run=_wt_ls)

    setup = commands.add_parser(
        "setup",
        help="install tools and dependencies for one checkout",
        description="Install a checkout's tools (mise) and dependencies (by lockfile). "
        "A step that fails warns, and the rest still run.",
    )
    setup.add_argument("path", metavar="<path>", nargs="?", default=".", help="the checkout (default: .)")
    setup.set_defaults(run=_setup)

    repo = commands.add_parser(
        "repo",
        help="add a repo to this machine",
        description="Add repos to this machine as checkouts at ~/<dir> or ~/<group>/<dir>.",
    )
    repo_commands = repo.add_subparsers(metavar="<command>", required=True)

    add = repo_commands.add_parser(
        "add",
        help="clone a repo and set it up",
        description="Clone a repo, retrying while new access takes effect, then set it up: "
        "copy .env.example to .env if there's no .env, then install tools and "
        "dependencies and run ./setup.sh. A setup step that fails warns, and the rest "
        "still run.",
    )
    add.add_argument(
        "clone_url",
        metavar="<clone-url>",
        help="e.g. https://github.com/owner/name or git@github.com:owner/name.git",
    )
    add.add_argument(
        "--dir",
        metavar="<dir>|<group>/<dir>",
        help="where under ~ to put it (default: the repo's name). Its databases are "
        "named after the last part, which must differ from every other checkout's",
    )
    add.add_argument(
        "--db",
        action="store_true",
        help="create a <dir>_dev database and write its settings to .env.local, so that "
        "devenv wt new copies it for each worktree",
    )
    add.add_argument(
        "--no-setup",
        dest="setup",
        action="store_false",
        help="don't install tools or dependencies or run ./setup.sh",
    )
    add.add_argument(
        "--check",
        action="store_true",
        help="only check that the repo can be added here, without cloning",
    )
    add.set_defaults(run=_repo_add)

    apply_cmd = commands.add_parser(
        "apply",
        help="install and update the dev user's tools, agent settings and shell files",
        description="Bring the dev user's environment up to date: dotfiles, mise's global "
        "tools, the agent CLIs and their settings, Playwright's Chromium, and the PATH in "
        "~/.zshenv and ~/.bashrc. It only installs and updates, never removes, and a second "
        "run changes nothing. A failed optional step (agent CLIs, Chromium) warns; a failed "
        "required one (dotfiles, mise tools, agent settings, shell files) makes it exit 1 "
        "after the rest have run. Run it as the dev user, not root.",
    )
    apply_cmd.set_defaults(run=_apply)

    upgrade_cmd = commands.add_parser(
        "upgrade",
        help="upgrade devenv, then apply with the new version",
        description="Upgrade devenv with uv tool upgrade, then run the new version's apply. "
        "This is how a machine picks up changes to devenv and its tool lists. If the "
        "upgrade fails, nothing is applied.",
    )
    upgrade_cmd.set_defaults(run=_upgrade)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    return guarded(lambda: args.run(args))
