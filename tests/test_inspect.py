from __future__ import annotations

import sys
from pathlib import Path

import pytest

from rewind.inspect import recorded_evidence, task_diff
from rewind.store import RewindError, initialize
from rewind.task import finish_task, run_check, start_task


def test_diff_and_evidence_show_the_recorded_work(git_repo: Path) -> None:
    paths = initialize(git_repo)
    task = start_task(paths, "Change the tracked value", ["tracked.txt"])
    (git_repo / "tracked.txt").write_text("changed\n", encoding="utf-8")
    check = run_check(
        paths,
        [sys.executable, "-c", "import sys; print('visible stdout'); print('visible stderr', file=sys.stderr)"],
    )
    finish_task(paths)

    patch = task_diff(paths, task["task_id"])
    assert "-initial" in patch
    assert "+changed" in patch
    assert "tracked.txt" in task_diff(paths, task["task_id"], stat=True)

    evidence = recorded_evidence(paths, task["task_id"])
    assert evidence["evidence_sha256"] == check["evidence_sha256"]
    assert evidence["output"]["stdout"] == "visible stdout\n"
    assert evidence["output"]["stderr"] == "visible stderr\n"
    assert (
        recorded_evidence(paths, task["task_id"], str(check["evidence_sha256"])[:12])[
            "event_id"
        ]
        == evidence["event_id"]
    )


def test_evidence_reader_rejects_tampered_or_unreferenced_objects(git_repo: Path) -> None:
    paths = initialize(git_repo)
    task = start_task(paths, "Record evidence", ["tracked.txt"])
    check = run_check(paths, [sys.executable, "-c", "print('ok')"])
    finish_task(paths)

    with pytest.raises(RewindError, match="not referenced"):
        recorded_evidence(paths, task["task_id"], "0" * 64)
    with pytest.raises(RewindError, match="at least 8"):
        recorded_evidence(paths, task["task_id"], "dead")

    (paths.objects / str(check["evidence_sha256"])).write_bytes(b"tampered")
    with pytest.raises(RewindError, match="Could not verify evidence"):
        recorded_evidence(paths, task["task_id"])
