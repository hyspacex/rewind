from __future__ import annotations

import sys
from pathlib import Path

import pytest

from rewind.crypto import canonical_bytes
from rewind.receipt import build_receipt, dependency_files, is_cache_artifact, path_matches
from rewind.report import render_report
from rewind.store import atomic_json, initialize, project, put_object
from rewind.task import finish_task, record_checkpoint, run_check, start_task, task_events

from conftest import git


def test_receipt_finds_scope_dependencies_and_stale_evidence(git_repo: Path) -> None:
    (git_repo / "demo_app/upload").mkdir(parents=True)
    (git_repo / "demo_app/upload/worker.py").write_text("VALUE = 1\n")
    (git_repo / "pyproject.toml").write_text("[project]\nname='fixture'\n")
    git(git_repo, "add", ".")
    git(git_repo, "commit", "-m", "add fixture")
    paths = initialize(git_repo)
    task = start_task(paths, "Add retries", ["demo_app/upload/**", "tests/**"])

    (git_repo / "demo_app/upload/worker.py").write_text("VALUE = 2\n")
    (git_repo / "tests").mkdir()
    (git_repo / "tests/test_sample.py").write_text("def test_ok():\n    assert True\n")
    (git_repo / "pyproject.toml").write_text("[project]\nname='fixture'\ndependencies=['backoff']\n")
    check = run_check(paths, [sys.executable, "-m", "pytest", "-q"])
    assert check["passed"]
    (git_repo / "deploy.yaml").write_text("replicas: 2\n")
    (git_repo / "demo_app/upload/worker.py").write_text("VALUE = 3\n")
    finish_task(paths)

    receipt = build_receipt(paths, task["task_id"])
    assert "deploy.yaml" in receipt["outside_scope"]
    assert "pyproject.toml" in receipt["dependency_files"]
    assert set(receipt["changed_after_passing_evidence"]) == {
        "demo_app/upload/worker.py",
        "deploy.yaml",
    }
    assert receipt["recommended_recovery"]["checkpoint_id"] == check["checkpoint_id"]
    assert receipt["outcome"] == "Completed with items to review"

    report = render_report(receipt, git_repo / "report.html")
    html = report.read_text()
    assert "<!doctype html>" in html
    assert "Outside declared scope" in html
    assert "1 test passed" in html
    assert "1 tests passed" not in html
    assert "Recommended tested state" in html
    assert "Branch-only recovery from a verified tested state" in html
    assert "-m rewind.cli recover" in html
    assert f"--task {task['task_id']}" in html
    assert "https://" not in html
    assert "<script" not in html


def test_protected_globs_are_signed_at_task_start(git_repo: Path) -> None:
    paths = initialize(git_repo)
    task = start_task(paths, "Protect deployment configuration", ["deploy/**"])
    atomic_json(paths.config, {"schema": "rewind.config.v1", "protected_globs": []})
    (git_repo / "deploy").mkdir()
    (git_repo / "deploy/service.yaml").write_text("replicas: 2\n", encoding="utf-8")
    run_check(paths, [sys.executable, "-c", "print('ok')"])
    finish_task(paths)

    receipt = build_receipt(paths, task["task_id"])
    assert receipt["protected_files"] == ["deploy/service.yaml"]
    start_event = next(
        event
        for event in task_events(paths, task["task_id"])
        if event["type"] == "task_started"
    )
    assert "deploy/**" in start_event["payload"]["protected_globs"]


def test_latest_failed_check_forces_needs_attention(git_repo: Path) -> None:
    paths = initialize(git_repo)
    task = start_task(paths, "Verify latest failure semantics", ["tests/**"])
    tests = git_repo / "tests"
    tests.mkdir()
    test_file = tests / "test_latest.py"
    test_file.write_text("def test_latest():\n    assert True\n")
    passing = run_check(paths, [sys.executable, "-m", "pytest", "-q"])
    assert passing["passed"]

    test_file.write_text("def test_latest():\n    assert False\n")
    failed = run_check(paths, [sys.executable, "-m", "pytest", "-q"])
    assert not failed["passed"]
    finish_task(paths)

    receipt = build_receipt(paths, task["task_id"])
    evidence = next(signal for signal in receipt["signals"] if signal["key"] == "evidence")
    assert receipt["outcome"] == "Needs attention"
    assert receipt["outcome_key"] == "fail"
    assert evidence["status"] == "FAIL"
    assert evidence["summary"] == "The most recent recorded check failed"
    assert receipt["recommended_recovery"]["checkpoint_id"] == passing["checkpoint_id"]
    assert receipt["recommended_recovery_kind"] == "test"


def test_active_receipt_never_claims_uncaptured_live_work_is_complete(
    git_repo: Path,
) -> None:
    paths = initialize(git_repo)
    task = start_task(paths, "Keep active receipts provisional", ["tracked.txt"])
    run_check(paths, [sys.executable, "-c", "print('ok')"])
    (git_repo / "tracked.txt").write_text("changed after check\n", encoding="utf-8")

    receipt = build_receipt(paths, task["task_id"])
    evidence = next(signal for signal in receipt["signals"] if signal["key"] == "evidence")
    assert receipt["finished"] is False
    assert receipt["outcome"] == "In progress"
    assert receipt["outcome_key"] == "warn"
    assert evidence["status"] == "WARN"
    assert "covers recorded checkpoints only" in evidence["summary"]
    assert "rewind finish" in evidence["summary"]


def test_incomplete_check_attempt_cannot_leave_an_earlier_pass_false_green(
    git_repo: Path,
) -> None:
    paths = initialize(git_repo)
    task = start_task(paths, "Detect an incomplete validation attempt", ["tracked.txt"])
    passing = run_check(paths, [sys.executable, "-c", "print('ok')"])
    record_checkpoint(paths, "Before check: missing-command", kind="pre_check")
    finish_task(paths)

    receipt = build_receipt(paths, task["task_id"])
    evidence = next(signal for signal in receipt["signals"] if signal["key"] == "evidence")
    assert receipt["outcome"] == "Needs attention"
    assert evidence["status"] == "FAIL"
    assert "did not produce command evidence" in evidence["summary"]
    assert receipt["recommended_recovery"]["checkpoint_id"] == passing["checkpoint_id"]
    assert "incomplete_check_attempt" in {
        issue["code"] for issue in receipt["audit"]["issues"]
    }


@pytest.mark.parametrize("damage", ["missing", "tampered"])
def test_unverifiable_evidence_never_recommends_recovery(
    git_repo: Path,
    damage: str,
) -> None:
    paths = initialize(git_repo)
    task = start_task(paths, "Reject unverifiable evidence", ["tracked.txt"])
    check = run_check(paths, [sys.executable, "-c", "print('ok')"])
    assert check["passed"]
    finish_task(paths)

    object_path = paths.objects / str(check["evidence_sha256"])
    if damage == "missing":
        object_path.unlink()
    else:
        object_path.write_bytes(b"tampered evidence")

    receipt = build_receipt(paths, task["task_id"])
    recovery = next(signal for signal in receipt["signals"] if signal["key"] == "recovery")
    assert receipt["outcome"] == "Needs attention"
    assert receipt["recommended_recovery"] is None
    assert receipt["recommended_recovery_kind"] is None
    assert receipt["passing_check_count"] == 0
    assert recovery["status"] == "NONE"
    assert "integrity verification" in recovery["summary"]

    report = render_report(receipt, git_repo / f"{damage}.html")
    html = report.read_text()
    assert "Recovery unavailable" in html
    assert "No recovery command is recommended." in html
    assert "Safe state" not in html
    assert "Safe recovery" not in html


def test_hash_valid_evidence_cannot_contradict_its_signed_pass_result(
    git_repo: Path,
) -> None:
    paths = initialize(git_repo)
    task = start_task(paths, "Reject semantic evidence mismatch", ["tracked.txt"])
    checkpoint = record_checkpoint(paths, "Before forged check", kind="pre_check")
    evidence = {
        "schema": "rewind.command-output.v1",
        "argv": ["false"],
        "exit_code": 1,
        "duration_ms": 1,
        "stdout": "",
        "stderr": "",
    }
    digest = put_object(paths, canonical_bytes(evidence))
    project(paths).append(
        "command_evidence_recorded",
        {
            "task_id": task["task_id"],
            "checkpoint_id": checkpoint["checkpoint_id"],
            "checkpoint_commit": checkpoint["commit"],
            "tree": checkpoint["tree"],
            "argv": ["false"],
            "exit_code": 1,
            "duration_ms": 1,
            "passed": True,
            "kind": "check",
            "evidence_sha256": digest,
        },
    )
    finish_task(paths)

    receipt = build_receipt(paths, task["task_id"])
    evidence_signal = next(
        signal for signal in receipt["signals"] if signal["key"] == "evidence"
    )
    assert receipt["outcome"] == "Needs attention"
    assert evidence_signal["status"] == "FAIL"
    assert "contradicts the recorded exit code" in evidence_signal["summary"]
    assert receipt["recommended_recovery"] is None
    assert "evidence_payload_mismatch" in {
        issue["code"] for issue in receipt["audit"]["issues"]
    }


def test_missing_checkpoint_ref_never_produces_recovery_ready(git_repo: Path) -> None:
    paths = initialize(git_repo)
    task = start_task(paths, "Reject a missing recovery ref", ["tracked.txt"])
    check = run_check(paths, [sys.executable, "-c", "print('ok')"])
    finish_task(paths)
    checkpoint = next(
        event["payload"]
        for event in task_events(paths, task["task_id"])
        if event["type"] == "checkpoint_recorded"
        and event["payload"]["checkpoint_id"] == check["checkpoint_id"]
    )
    git(git_repo, "update-ref", "-d", checkpoint["ref"])

    receipt = build_receipt(paths, task["task_id"])
    recovery = next(signal for signal in receipt["signals"] if signal["key"] == "recovery")
    assert receipt["outcome"] == "Needs attention"
    assert receipt["recommended_recovery"] is None
    assert recovery["status"] == "FAIL"
    assert "checkpoint ref is missing" in recovery["summary"]
    assert "checkpoint_integrity_failed" in {
        issue["code"] for issue in receipt["audit"]["issues"]
    }


def test_arbitrary_check_uses_checked_not_tested_recovery_wording(git_repo: Path) -> None:
    paths = initialize(git_repo)
    task = start_task(paths, "Record an arbitrary verification", ["tracked.txt"])
    check = run_check(paths, [sys.executable, "-c", "print('verified')"])
    assert check["passed"]
    finish_task(paths)

    receipt = build_receipt(paths, task["task_id"])
    recovery = next(signal for signal in receipt["signals"] if signal["key"] == "recovery")
    assert receipt["recommended_recovery_kind"] == "check"
    assert "passing check evidence" in recovery["summary"]
    assert "tested" not in recovery["summary"]

    report = render_report(
        receipt,
        git_repo / "check-report.html",
        rewind_command="venv/bin/rewind",
    )
    html = report.read_text()
    assert "Recommended checked state" in html
    assert "Branch-only recovery from a verified checked state" in html
    assert "verified tested state" not in html
    assert "venv/bin/rewind recover" in html
    assert "1 passing check" in html
    assert "1 passing checks" not in html


def test_later_arbitrary_check_does_not_relabel_earlier_test_as_recovery_basis(
    git_repo: Path,
) -> None:
    paths = initialize(git_repo)
    task = start_task(paths, "Keep test and check semantics separate", ["tracked.txt"])
    test_file = git_repo / "test_semantics.py"
    test_file.write_text("def test_semantics():\n    assert True\n", encoding="utf-8")
    run_check(paths, [sys.executable, "-m", "pytest", "-q", str(test_file)])
    run_check(paths, [sys.executable, "-c", "print('generic verification')"])
    finish_task(paths)

    receipt = build_receipt(paths, task["task_id"])
    assert receipt["recommended_recovery_kind"] == "check"
    assert receipt["test_count"] is None
    assert receipt["passing_check_count"] == 2

    report = render_report(receipt, git_repo / "mixed-check-report.html")
    html = report.read_text()
    assert "Recommended checked state" in html
    assert "2 passing checks" in html
    assert "tests passed" not in html


def test_duplicate_checkpoint_id_disables_recovery(git_repo: Path) -> None:
    paths = initialize(git_repo)
    task = start_task(paths, "Reject ambiguous recovery", ["tracked.txt"])
    check = run_check(paths, [sys.executable, "-c", "print('verified')"])
    original = next(
        event
        for event in task_events(paths, task["task_id"])
        if event["type"] == "checkpoint_recorded"
        and event["payload"]["checkpoint_id"] == check["checkpoint_id"]
    )
    project(paths).append(
        "checkpoint_recorded",
        {**original["payload"], "label": "Concurrent duplicate"},
    )
    finish_task(paths)

    receipt = build_receipt(paths, task["task_id"])
    issue_codes = {issue["code"] for issue in receipt["audit"]["issues"]}
    recovery = next(signal for signal in receipt["signals"] if signal["key"] == "recovery")
    assert receipt["outcome"] == "Needs attention"
    assert receipt["recommended_recovery"] is None
    assert recovery["status"] == "FAIL"
    assert "duplicate_checkpoint_id" in issue_codes
    assert "ambiguous_recovery_checkpoint" in issue_codes


def test_dependency_patterns() -> None:
    assert dependency_files(
        [
            "frontend/package-lock.json",
            "requirements-dev.txt",
            "src/main.py",
            "Cargo.toml",
            "go.sum",
        ]
    ) == [
        "frontend/package-lock.json",
        "requirements-dev.txt",
        "Cargo.toml",
        "go.sum",
    ]


def test_hidden_paths_keep_their_leading_dot_for_scope_matching() -> None:
    assert path_matches(".codex/config.toml", ".codex/**")
    assert path_matches(".gitignore", ".gitignore")
    assert not path_matches("codex/config.toml", ".codex/**")


def test_cache_artifacts_are_excluded_from_receipt_analysis(git_repo: Path) -> None:
    (git_repo / "pkg").mkdir()
    (git_repo / "pkg/mod.py").write_text("VALUE = 1\n")
    git(git_repo, "add", ".")
    git(git_repo, "commit", "-m", "add pkg")
    paths = initialize(git_repo)
    task = start_task(paths, "Touch module", ["pkg/**"])

    (git_repo / "pkg/mod.py").write_text("VALUE = 2\n")
    check = run_check(paths, [sys.executable, "-c", "print('ok')"])
    assert check["passed"]
    cache_dir = git_repo / "pkg/__pycache__"
    cache_dir.mkdir()
    (cache_dir / "mod.cpython-312.pyc").write_bytes(b"\x00fake bytecode")
    (git_repo / "stray.pyc").write_bytes(b"\x00stray")
    finish_task(paths)

    receipt = build_receipt(paths, task["task_id"])
    assert receipt["changed_files"] == ["pkg/mod.py"]
    assert receipt["outside_scope"] == []
    assert all(
        "__pycache__" not in path and not path.endswith(".pyc")
        for path in receipt["changed_after_passing_evidence"]
    )


def test_is_cache_artifact_patterns() -> None:
    assert is_cache_artifact("pkg/__pycache__/mod.cpython-312.pyc")
    assert is_cache_artifact("stray.pyo")
    assert is_cache_artifact(".pytest_cache/v/cache/lastfailed")
    assert not is_cache_artifact("pkg/mod.py")
    assert not is_cache_artifact("docs/pycache_notes.md")
