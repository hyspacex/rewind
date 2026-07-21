from __future__ import annotations

import json
import sys
from pathlib import Path

from rewind.receipt import build_receipt
from rewind.store import initialize
from rewind.task import finish_task, run_check, start_task


def test_missing_executable_is_recorded_as_latest_failed_evidence(git_repo: Path) -> None:
    paths = initialize(git_repo)
    task = start_task(paths, "Verify a command launch failure", ["tracked.txt"])

    passing = run_check(paths, [sys.executable, "-c", "print('ok')"])
    missing = run_check(paths, [str(git_repo / "definitely-not-installed")])

    assert passing["passed"] is True
    assert missing["passed"] is False
    assert missing["exit_code"] == 127
    assert "Could not execute" in str(missing["launch_error"])

    evidence_path = paths.objects / str(missing["evidence_sha256"])
    evidence = json.loads(evidence_path.read_bytes())
    assert evidence["argv"] == [str(git_repo / "definitely-not-installed")]
    assert evidence["exit_code"] == 127
    assert evidence["launch_error"]["type"] == "FileNotFoundError"

    finish_task(paths)
    receipt = build_receipt(paths, task["task_id"])
    evidence_signal = next(signal for signal in receipt["signals"] if signal["key"] == "evidence")
    assert evidence_signal["status"] == "FAIL"
    assert evidence_signal["summary"] == "The most recent recorded check failed"
    assert receipt["outcome"] == "Needs attention"

