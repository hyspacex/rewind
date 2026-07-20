from __future__ import annotations

import subprocess
from pathlib import Path

from rewind.git_state import checkpoint, create_recovery_branch
from rewind.store import initialize

from conftest import git


def test_checkpoint_preserves_real_git_state_and_captures_untracked(git_repo: Path) -> None:
    initialize(git_repo)
    (git_repo / "tracked.txt").write_text("working tree\n")
    (git_repo / "untracked.txt").write_text("included\n")
    git(git_repo, "add", "tracked.txt")
    before_status = subprocess.run(
        ["git", "status", "--porcelain=v1", "-z"],
        cwd=git_repo,
        capture_output=True,
        check=True,
    ).stdout
    before_branch = git(git_repo, "branch", "--show-current")
    before_head = git(git_repo, "rev-parse", "HEAD")
    before_index = git(git_repo, "write-tree")

    snapshot = checkpoint(git_repo, "task_test", "cp_01", "test snapshot")

    assert subprocess.run(
        ["git", "status", "--porcelain=v1", "-z"],
        cwd=git_repo,
        capture_output=True,
        check=True,
    ).stdout == before_status
    assert git(git_repo, "branch", "--show-current") == before_branch
    assert git(git_repo, "rev-parse", "HEAD") == before_head
    assert git(git_repo, "write-tree") == before_index
    assert git(git_repo, "show", f"{snapshot.commit}:untracked.txt") == "included"
    assert git(git_repo, "show", f"{snapshot.commit}:tracked.txt") == "working tree"
    assert subprocess.run(
        ["git", "cat-file", "-e", f"{snapshot.commit}:.rewind/config.json"],
        cwd=git_repo,
        capture_output=True,
        check=False,
    ).returncode != 0


def test_recovery_creates_branch_without_switching(git_repo: Path) -> None:
    initialize(git_repo)
    (git_repo / "untracked.txt").write_text("snapshot\n")
    snapshot = checkpoint(git_repo, "task_test", "cp_01", "safe")
    branch_before = git(git_repo, "branch", "--show-current")
    head_before = git(git_repo, "rev-parse", "HEAD")
    status_before = git(git_repo, "status", "--porcelain=v1")

    create_recovery_branch(git_repo, "rewind/test-safe", snapshot.commit)

    assert git(git_repo, "rev-parse", "rewind/test-safe") == snapshot.commit
    assert git(git_repo, "branch", "--show-current") == branch_before
    assert git(git_repo, "rev-parse", "HEAD") == head_before
    assert git(git_repo, "status", "--porcelain=v1") == status_before
