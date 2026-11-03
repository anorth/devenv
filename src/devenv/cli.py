"""The `devenv` command line."""

import argparse
from pathlib import Path

from devenv.git import current_checkout
from devenv.setup import setup_checkout
from devenv.ui import guarded
from devenv.worktrees import list_worktrees, new_worktree, remove_worktree

DESCRIPTION = """\
Manage checkouts, worktrees and the user-level environment of a dev VM.
It keeps no state of its own: git's worktree list, each worktree's .env,
Postgres and its naming rules say it all.
"""

WT_DESCRIPTION = """\
Create, remove or list worktrees of the repo you're in: run it from the main
checkout or any of its worktrees. Each worktree lives next to the main
checkout, in <checkout>-wt/<branch-slug>, with its own dev server PORT in its
.env. If a <dir>_dev database exists, each worktree also gets a copy of it, with
DATABASE_URL in its .env. Databases are named after the checkout's directory,
so give each repo on the machine a different one.
"""


def _wt_new(args: argparse.Namespace) -> None:
    new_worktree(current_checkout(), args.branch, args.base)


def _wt_rm(args: argparse.Namespace) -> None:
    remove_worktree(current_checkout(), args.branch, args.force)


def _wt_ls(args: argparse.Namespace) -> None:
    list_worktrees(current_checkout())


def _setup(args: argparse.Namespace) -> None:
    setup_checkout(Path(args.path))


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
        description="Create a worktree for <branch>, with its own port and, "
        "if there's a <dir>_dev database, its own copy of it.",
    )
    new.add_argument(
        "branch",
        metavar="<branch>",
        help="branch to work on: checked out if it exists locally or on origin, "
        "otherwise created from --base",
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
        description="Remove the branch's worktree and drop its database, if it has one. "
        "The branch is deleted too if it's merged into the default branch.",
    )
    rm.add_argument("branch", metavar="<branch>", help="branch whose worktree to remove")
    rm.add_argument(
        "--force",
        action="store_true",
        help="remove even with uncommitted changes, and drop the database even while it's in use",
    )
    rm.set_defaults(run=_wt_rm)

    ls = wt_commands.add_parser(
        "ls",
        help="show worktrees with their port and database",
        description="Show the repo's worktrees with their port and database.",
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
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    return guarded(lambda: args.run(args))
