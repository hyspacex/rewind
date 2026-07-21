"""Non-destructive Git snapshots and recovery branches."""

from __future__ import annotations

import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .store import RewindError


@dataclass(frozen=True)
class GitCheckpoint:
    commit: str
    tree: str
    ref: str
    head: str


def _git(
    root: Path,
    *args: str,
    env: dict[str, str] | None = None,
    input_text: str | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    process = subprocess.run(
        ["git", *args],
        cwd=root,
        env=env,
        input=input_text,
        text=True,
        capture_output=True,
        check=False,
    )
    if check and process.returncode:
        message = process.stderr.strip() or process.stdout.strip()
        raise RewindError(f"Git command failed (`git {' '.join(args)}`): {message}")
    return process


def head_commit(root: Path) -> str:
    return _git(root, "rev-parse", "--verify", "HEAD").stdout.strip()


def current_branch(root: Path) -> str:
    return _git(root, "branch", "--show-current").stdout.strip()


def status_bytes(root: Path) -> bytes:
    return subprocess.run(
        ["git", "status", "--porcelain=v1", "-z"],
        cwd=root,
        capture_output=True,
        check=True,
    ).stdout


def index_tree(root: Path) -> str:
    return _git(root, "write-tree").stdout.strip()


def checkpoint(
    root: Path,
    task_id: str,
    checkpoint_id: str,
    label: str,
) -> GitCheckpoint:
    """Snapshot the working tree under a private ref without touching the real index."""
    head = head_commit(root)
    ref = f"refs/rewind/checkpoints/{task_id}/{checkpoint_id}"
    temp_dir = root / ".rewind" / "tmp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    fd, index_name = tempfile.mkstemp(prefix="index-", dir=temp_dir)
    os.close(fd)
    os.unlink(index_name)
    env = os.environ.copy()
    env["GIT_INDEX_FILE"] = index_name
    try:
        _git(root, "read-tree", "HEAD", env=env)
        _git(root, "add", "-A", "--", ".", env=env)
        # Remove recorder-local files from the temporary index even when the
        # host repository does not ignore `.rewind/`. This reset affects only
        # GIT_INDEX_FILE, never the developer's real index or working tree.
        _git(root, "reset", "-q", "HEAD", "--", ".rewind", env=env)
        tree = _git(root, "write-tree", env=env).stdout.strip()
        commit_message = f"Rewind checkpoint {checkpoint_id}: {label}\n\nTask: {task_id}\n"
        commit = _git(
            root,
            "commit-tree",
            tree,
            "-p",
            head,
            input_text=commit_message,
        ).stdout.strip()
        _git(root, "update-ref", ref, commit)
        return GitCheckpoint(commit=commit, tree=tree, ref=ref, head=head)
    finally:
        for suffix in ("", ".lock"):
            candidate = Path(index_name + suffix)
            if candidate.exists():
                candidate.unlink()


def changed_files(root: Path, old_tree: str, new_tree: str) -> list[str]:
    process = subprocess.run(
        ["git", "diff", "--name-only", "-z", old_tree, new_tree],
        cwd=root,
        capture_output=True,
        check=False,
    )
    if process.returncode:
        raise RewindError(process.stderr.decode("utf-8", errors="replace").strip())
    return sorted(
        item.decode("utf-8", errors="surrogateescape")
        for item in process.stdout.split(b"\0")
        if item
    )


def tree_for_commit(root: Path, commit: str) -> str:
    return _git(root, "rev-parse", f"{commit}^{{tree}}").stdout.strip()


def checkpoint_integrity_issue(
    root: Path,
    *,
    commit: str,
    tree: str,
    ref: str,
) -> str | None:
    """Return why a signed checkpoint is not recoverable, or ``None``."""
    if not ref.startswith("refs/rewind/checkpoints/"):
        return f"checkpoint ref is outside Rewind's private namespace: {ref}"
    resolved = _git(root, "rev-parse", "--verify", ref, check=False)
    if resolved.returncode:
        return f"checkpoint ref is missing: {ref}"
    if resolved.stdout.strip() != commit:
        return f"checkpoint ref {ref} does not point at its signed commit"
    actual_tree = _git(root, "rev-parse", "--verify", f"{commit}^{{tree}}", check=False)
    if actual_tree.returncode:
        return f"checkpoint commit is missing or invalid: {commit}"
    if actual_tree.stdout.strip() != tree:
        return f"checkpoint commit {commit[:12]} does not contain its signed tree"
    return None


def create_recovery_branch(root: Path, name: str, commit: str) -> None:
    if not name.startswith("rewind/"):
        raise RewindError("Recovery branch names must start with `rewind/`.")
    if _git(root, "check-ref-format", "--branch", name, check=False).returncode:
        raise RewindError(f"Invalid branch name: {name}")
    if _git(root, "show-ref", "--verify", "--quiet", f"refs/heads/{name}", check=False).returncode == 0:
        raise RewindError(f"Branch already exists: {name}")
    _git(root, "branch", name, commit)
