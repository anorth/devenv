"""Installing one checkout's tools and dependencies."""

import shutil
from pathlib import Path

from devenv.system import call
from devenv.ui import Die, log, warn


def try_step(cmd: list[str], cwd: Path) -> None:
    """Run one setup step, warning on failure: the checkout is still usable."""
    label = " ".join(cmd)
    log(label)
    if not call(cmd, cwd=cwd):
        warn(f"failed: {label} (continuing)")


def _yarn_install() -> list[str]:
    return ["yarn", "install"] if shutil.which("yarn") else ["npx", "--yes", "yarn", "install"]


def setup_checkout(path: Path) -> None:
    """Install a checkout's tools (mise) and dependencies (by lockfile)."""
    if not path.is_dir():
        raise Die(f"{path} isn't a directory")

    def has(name: str) -> bool:
        return (path / name).is_file()

    mise_config = has("mise.toml") or has(".mise.toml")
    if mise_config:
        try_step(["mise", "trust"], path)
    if mise_config or has(".tool-versions"):
        try_step(["mise", "install"], path)
    if has("pnpm-lock.yaml"):
        try_step(["pnpm", "install"], path)
    elif has("package-lock.json"):
        try_step(["npm", "ci"], path)
    elif has("yarn.lock"):
        try_step(_yarn_install(), path)
    if has("uv.lock"):
        try_step(["uv", "sync"], path)
