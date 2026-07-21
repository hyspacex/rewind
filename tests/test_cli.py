from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from typer.testing import CliRunner

from rewind.cli import app
from rewind.store import RewindPaths, initialize, project
from rewind.task import finish_task, run_check, start_task, task_events

runner = CliRunner()


def test_status_before_init_explains_next_step(
    git_repo: Path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(git_repo)

    result = runner.invoke(app, ["status"])

    assert result.exit_code == 0
    assert "not initialized in this repository" in result.stdout
    assert "rewind init" in result.stdout
    assert "Mutable task state" not in result.stdout


def test_start_before_init_uses_the_same_clear_guidance(
    git_repo: Path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(git_repo)

    result = runner.invoke(
        app,
        ["start", "--intent", "Try first run", "--allow", "tracked.txt"],
    )

    assert result.exit_code == 1
    assert "Rewind is not initialized" in result.stdout
    assert "rewind init" in result.stdout
    assert "Mutable task state" not in result.stdout
    assert not (git_repo / ".rewind/tmp").exists()


def test_init_preserves_committed_shared_config_for_a_fresh_clone(
    git_repo: Path,
    monkeypatch,
) -> None:
    local = git_repo / ".rewind"
    local.mkdir()
    config = {
        "schema": "rewind.config.v1",
        "protected_globs": ["ops/**"],
    }
    (local / "config.json").write_text(
        json.dumps(config, indent=2) + "\n",
        encoding="utf-8",
    )
    (local / ".gitignore").write_text("# shared policy\ncustom-cache/\n", encoding="utf-8")
    monkeypatch.chdir(git_repo)

    before = runner.invoke(app, ["status"])
    initialized = runner.invoke(app, ["init"])

    assert before.exit_code == 0
    assert "Local Rewind recorder state is not initialized" in before.stdout
    assert "rewind init" in before.stdout
    assert initialized.exit_code == 0
    assert json.loads((local / "config.json").read_text(encoding="utf-8")) == config
    ignore_text = (local / ".gitignore").read_text(encoding="utf-8")
    assert "# shared policy" in ignore_text
    assert "custom-cache/" in ignore_text
    for required in ("events.jsonl", "state.json", "objects/", "keys/", "tmp/"):
        assert required in ignore_text.splitlines()
    assert (local / "keys/recorder.key").is_file()
    assert (local / "events.jsonl").is_file()


def test_receipt_uses_concise_singular_copy(
    git_repo: Path,
    monkeypatch,
) -> None:
    initialize(git_repo)
    paths = RewindPaths(git_repo)
    start_task(paths, "Record one test", ["**"])
    run_check(
        paths,
        [
            sys.executable,
            "-m",
            "pytest",
            "--version",
        ],
    )
    monkeypatch.chdir(git_repo)

    result = runner.invoke(app, ["receipt"])

    assert result.exit_code == 0
    assert "1 passing check" in result.stdout
    assert "1 passing checks" not in result.stdout
    assert "item(s)" not in result.stdout
    assert "with items to review with" not in result.stdout
    assert "Recovery state:" in result.stdout


def test_evidence_displays_verified_terminal_safe_output(
    git_repo: Path,
    monkeypatch,
) -> None:
    initialize(git_repo)
    paths = RewindPaths(git_repo)
    start_task(paths, "Inspect command output", ["**"])
    recorded = run_check(
        paths,
        [
            sys.executable,
            "-c",
            "print('visible'); print('\\x1b[31mnot-replayed\\x1b[0m')",
        ],
    )
    monkeypatch.chdir(git_repo)

    result = runner.invoke(
        app,
        ["evidence", "--digest", str(recorded["evidence_sha256"])],
    )

    assert result.exit_code == 0
    assert "VERIFIED EVIDENCE" in result.stdout
    assert "visible" in result.stdout
    assert "\\x1b[31mnot-replayed\\x1b[0m" in result.stdout
    assert "\x1b[31m" not in result.stdout
    assert str(recorded["evidence_sha256"]) in result.stdout


def test_diff_displays_exact_recorded_patch(
    git_repo: Path,
    monkeypatch,
) -> None:
    initialize(git_repo)
    paths = RewindPaths(git_repo)
    start_task(paths, "Inspect exact changes", ["tracked.txt"])
    (git_repo / "tracked.txt").write_text("changed\n", encoding="utf-8")
    run_check(paths, [sys.executable, "-c", "print('checked')"])
    monkeypatch.chdir(git_repo)

    result = runner.invoke(app, ["diff"])
    stat_result = runner.invoke(app, ["diff", "--stat"])

    assert result.exit_code == 0
    assert "-initial" in result.stdout
    assert "+changed" in result.stdout
    assert stat_result.exit_code == 0
    assert "tracked.txt" in stat_result.stdout


def test_evidence_refuses_a_tampered_object(
    git_repo: Path,
    monkeypatch,
) -> None:
    initialize(git_repo)
    paths = RewindPaths(git_repo)
    start_task(paths, "Reject tampered output", ["**"])
    recorded = run_check(paths, [sys.executable, "-c", "print('original')"])
    (paths.objects / str(recorded["evidence_sha256"])).write_text("tampered\n")
    monkeypatch.chdir(git_repo)

    result = runner.invoke(app, ["evidence"])

    assert result.exit_code == 1
    assert "failed its" in result.stdout
    assert "SHA-256 check" in result.stdout


def test_audit_cli_records_a_replayable_protected_action(
    git_repo: Path,
    monkeypatch,
) -> None:
    initialize(git_repo)
    paths = RewindPaths(git_repo)
    start_task(paths, "Record protected action", ["tracked.txt"])
    artifact = git_repo / "artifact.bin"
    artifact.write_bytes(b"release")
    monkeypatch.chdir(git_repo)

    check = runner.invoke(
        app,
        [
            "run",
            "--action",
            "release_01",
            "--",
            sys.executable,
            "-c",
            "print('verified')",
        ],
    )
    artifact_result = runner.invoke(
        app,
        [
            "audit",
            "artifact",
            "release_01",
            str(artifact),
            "--proposer",
            "build-agent",
        ],
    )
    approval = runner.invoke(
        app,
        ["approve", "release_01"],
    )
    policy = runner.invoke(app, ["audit", "policy", "v2"])
    role = runner.invoke(
        app,
        ["audit", "role-grant", "deploy-agent", "deployer"],
    )
    deployment = runner.invoke(
        app,
        [
            "audit",
            "deployment",
            "release_01",
            "--actor",
            "deploy-agent",
        ],
    )
    replay = runner.invoke(app, ["replay", "release_01"])

    assert check.exit_code == 0
    assert "action" in check.stdout
    assert "release_01" in check.stdout
    assert artifact_result.exit_code == 0
    assert approval.exit_code == 0
    assert policy.exit_code == 0
    assert role.exit_code == 0
    assert deployment.exit_code == 0
    assert replay.exit_code == 0
    assert "L3 As-of Authority" in replay.stdout


def test_recover_rejects_an_ambiguous_checkpoint_id(
    git_repo: Path,
    monkeypatch,
) -> None:
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
        {**original["payload"], "label": "Duplicate checkpoint ID"},
    )
    monkeypatch.chdir(git_repo)

    result = runner.invoke(
        app,
        [
            "recover",
            str(check["checkpoint_id"]),
            "--branch",
            "rewind/ambiguous",
        ],
    )

    assert result.exit_code == 1
    assert "is ambiguous" in result.stdout
    assert subprocess.run(
        ["git", "show-ref", "--verify", "--quiet", "refs/heads/rewind/ambiguous"],
        cwd=git_repo,
        check=False,
    ).returncode == 1


def test_recover_rejects_a_missing_private_checkpoint_ref(
    git_repo: Path,
    monkeypatch,
) -> None:
    paths = initialize(git_repo)
    task = start_task(paths, "Reject a missing checkpoint ref", ["tracked.txt"])
    check = run_check(paths, [sys.executable, "-c", "print('verified')"])
    checkpoint = next(
        event["payload"]
        for event in task_events(paths, task["task_id"])
        if event["type"] == "checkpoint_recorded"
        and event["payload"]["checkpoint_id"] == check["checkpoint_id"]
    )
    subprocess.run(
        ["git", "update-ref", "-d", str(checkpoint["ref"])],
        cwd=git_repo,
        check=True,
    )
    monkeypatch.chdir(git_repo)

    result = runner.invoke(
        app,
        [
            "recover",
            str(check["checkpoint_id"]),
            "--branch",
            "rewind/missing-ref",
        ],
    )

    assert result.exit_code == 1
    assert "failed integrity verification" in result.stdout
    assert "checkpoint ref is" in result.stdout
    assert "missing:" in result.stdout


def test_recover_task_option_disambiguates_checkpoint_ids_across_tasks(
    git_repo: Path,
    monkeypatch,
) -> None:
    paths = initialize(git_repo)
    first_task = start_task(paths, "First task", ["tracked.txt"])
    (git_repo / "tracked.txt").write_text("first\n", encoding="utf-8")
    first_check = run_check(paths, [sys.executable, "-c", "print('first')"])
    finish_task(paths)

    second_task = start_task(paths, "Second task", ["tracked.txt"])
    (git_repo / "tracked.txt").write_text("second\n", encoding="utf-8")
    second_check = run_check(paths, [sys.executable, "-c", "print('second')"])
    finish_task(paths)
    assert first_check["checkpoint_id"] == second_check["checkpoint_id"] == "cp_02"
    monkeypatch.chdir(git_repo)

    result = runner.invoke(
        app,
        [
            "recover",
            "cp_02",
            "--task",
            str(first_task["task_id"]),
            "--branch",
            "rewind/from-first-task",
        ],
    )

    assert result.exit_code == 0
    branch_commit = subprocess.run(
        ["git", "rev-parse", "rewind/from-first-task"],
        cwd=git_repo,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()
    assert branch_commit == first_check["checkpoint_commit"]
    assert branch_commit != second_check["checkpoint_commit"]
    assert first_task["task_id"] != second_task["task_id"]
