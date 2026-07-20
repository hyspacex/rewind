from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


def git(root: Path, *args: str) -> str:
    process = subprocess.run(
        ["git", *args],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )
    if process.returncode:
        raise AssertionError(process.stderr)
    return process.stdout.strip()


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-b", "main")
    git(root, "config", "user.name", "Rewind Tests")
    git(root, "config", "user.email", "tests@rewind.local")
    (root / "tracked.txt").write_text("initial\n")
    (root / ".gitignore").write_text("__pycache__/\n*.py[cod]\n.pytest_cache/\n")
    git(root, "add", "tracked.txt", ".gitignore")
    git(root, "commit", "-m", "initial")
    return root
