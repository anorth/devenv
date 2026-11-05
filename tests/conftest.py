import os
import shutil
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

SRC = Path(__file__).parent.parent / "src"


def git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True)


def git_out(cwd, *args):
    result = subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True)
    return result.stdout.strip()


@dataclass
class Machine:
    """A temporary home, with `devenv` run in it as a separate process."""

    home: Path
    bin: Path
    env: dict

    def run(self, *args, cwd=None):
        return subprocess.run(
            [sys.executable, "-m", "devenv", *args],
            cwd=cwd or self.home,
            env=self.env,
            capture_output=True,
            text=True,
        )

    def ok(self, *args, cwd=None):
        result = self.run(*args, cwd=cwd)
        assert result.returncode == 0, result.stdout + result.stderr
        return result

    def stub(self, name, script):
        path = self.bin / name
        path.write_text(f"#!/bin/sh\n{script}\n")
        path.chmod(path.stat().st_mode | stat.S_IEXEC)

    def origin(self, name, files=None):
        """A bare repo with one commit, outside the home, and its file:// URL."""
        bare = self.home.parent / "origins" / f"{name}.git"
        bare.parent.mkdir(exist_ok=True)
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True, env=self.env)
        seed = self.home.parent / "seeds" / name
        seed.mkdir(parents=True)
        self._commit(seed, files)
        git(seed, "remote", "add", "origin", str(bare))
        git(seed, "push", "-q", "origin", "main")
        return f"file://{bare}"

    def repo(self, rel="app", origin=False):
        """A main checkout with one commit, cloned from a bare origin if asked."""
        path = self.home / rel
        if origin:
            url = self.origin(path.name)
            path.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(["git", "clone", "-q", url, str(path)], check=True, env=self.env)
        else:
            path.mkdir(parents=True)
            self._commit(path)
        return path

    def _commit(self, path, files=None):
        subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True, env=self.env)
        (path / "README").write_text("hello\n")
        for name, content in (files or {}).items():
            (path / name).write_text(content)
        subprocess.run(["git", "-C", str(path), "add", "-f", "."], check=True, env=self.env)
        subprocess.run(["git", "-C", str(path), "commit", "-qm", "first"], check=True, env=self.env)


def _machine(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    # The tests' own git commands must see this home too, not the real one.
    monkeypatch.setenv("HOME", str(home))
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (home / ".gitconfig").write_text(
        "[user]\n\tname = Test\n\temail = test@example.com\n[init]\n\tdefaultBranch = main\n"
        "[core]\n\texcludesFile = ~/.gitignore_global\n"
    )
    (home / ".gitignore_global").write_text(".env\n.env.local\n")
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "PYTHONPATH": str(SRC),
        "GIT_CONFIG_NOSYSTEM": "1",
    }
    env.pop("GIT_CONFIG_GLOBAL", None)
    machine = Machine(home, bin_dir, env)
    machine.stub("tailscale", "exit 1")
    machine.stub("ss", f'cat "{bin_dir}/ss-output" 2>/dev/null; exit 0')
    return machine


@pytest.fixture
def machine(tmp_path, monkeypatch):
    """A machine with no template databases, whatever is really installed."""
    m = _machine(tmp_path, monkeypatch)
    # An empty answer means no such database.
    m.stub("psql", "cat >/dev/null; exit 0")
    return m


@pytest.fixture
def pg_machine(tmp_path, monkeypatch):
    """A machine using the real Postgres, skipped when there isn't one."""
    if not shutil.which("psql") or not shutil.which("createdb"):
        pytest.skip("Postgres isn't installed")
    check = subprocess.run(["psql", "-X", "-At", "-d", "postgres", "-c", "SELECT 1"], capture_output=True)
    if check.returncode != 0:
        pytest.skip("Postgres isn't available")
    return _machine(tmp_path, monkeypatch)
