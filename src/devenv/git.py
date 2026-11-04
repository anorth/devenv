"""Checkouts: finding them, and running git in them."""

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from devenv import rules
from devenv.system import call, capture
from devenv.ui import Die, log


@dataclass(frozen=True)
class Checkout:
    """A repo's main checkout, which any of its worktrees can lead back to."""

    main: Path

    @property
    def dir(self) -> str:
        return self.main.name

    def worktree_path(self, name: str) -> Path:
        return self.main.parent / name

    def git(self, *args: str) -> bool:
        """Run git with its output on the terminal; say whether it succeeded."""
        return call(["git", "-C", str(self.main), *args])

    def git_output(self, *args: str) -> subprocess.CompletedProcess[str]:
        return capture(["git", "-C", str(self.main), *args])

    def fetch_origin(self) -> bool:
        """Fetch origin, if there is one; False if that failed."""
        if self.git_output("remote", "get-url", "origin").returncode != 0:
            return True
        log("Fetching origin")
        return self.git("fetch", "--quiet", "--prune", "origin")

    def has_ref(self, ref: str) -> bool:
        return self.git_output("show-ref", "--verify", "--quiet", ref).returncode == 0

    def worktrees(self) -> list[rules.Worktree]:
        return rules.parse_worktree_list(self.git_output("worktree", "list", "--porcelain").stdout)

    def worktree_named(self, name: str) -> rules.Worktree | None:
        """A worktree by name, which is its directory's: nothing else records it."""
        for worktree in self.worktrees():
            if Path(worktree.path).name == name:
                return worktree
        return None

    def worktree_for_branch(self, branch: str) -> Path | None:
        for worktree in self.worktrees():
            if worktree.branch == branch:
                return Path(worktree.path)
        return None

    def default_branch(self) -> str:
        """Origin's default branch, or main when origin doesn't say."""
        result = self.git_output("symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD")
        return result.stdout.strip().removeprefix("origin/") if result.returncode == 0 else "main"


def current_checkout() -> Checkout:
    """The repo you're in. Its main checkout is where the shared .git lives."""
    result = capture(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"])
    if result.returncode != 0:
        raise Die("run devenv inside a git checkout, e.g. cd ~/<repo>")
    common = Path(result.stdout.rstrip("\n"))
    if common.name != ".git":
        raise Die(
            f"the repo's git directory, {common}, isn't the .git of a checkout; "
            "bare repos, submodules and --separate-git-dir clones aren't supported"
        )
    return Checkout(common.parent)


def _subdirs(path: Path) -> list[Path]:
    try:
        with os.scandir(path) as entries:
            return [Path(e.path) for e in entries if e.is_dir(follow_symlinks=False)]
    except OSError:
        return []


def find_checkouts(home: Path) -> list[Path]:
    """Main checkouts up to one folder below home.

    A worktree's .git is a file, so requiring a directory finds each repo once.
    """
    candidates = (d for top in _subdirs(home) for d in (top, *_subdirs(top)))
    return [d for d in candidates if (d / ".git").is_dir() and not (d / ".git").is_symlink()]
