from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

import rewind.task as task_module
from rewind.events import verify_log
from rewind.store import RewindError, RewindPaths, initialize, project
from rewind.task import events, record_checkpoint, start_task, status

from conftest import git


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _release_processes(processes: list[subprocess.Popen[str]], ready_dir: Path, go: Path) -> None:
    deadline = time.monotonic() + 15
    while len(list(ready_dir.iterdir())) != len(processes):
        if time.monotonic() >= deadline:
            for process in processes:
                process.kill()
            raise AssertionError("worker processes did not become ready")
        time.sleep(0.01)
    go.write_text("go\n", encoding="utf-8")


def _worker_env() -> dict[str, str]:
    env = os.environ.copy()
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = str(PROJECT_ROOT) if not existing else f"{PROJECT_ROOT}{os.pathsep}{existing}"
    return env


def test_parallel_checkpoints_get_unique_ids_and_private_refs(git_repo: Path, tmp_path: Path) -> None:
    paths = initialize(git_repo)
    start_task(paths, "Parallel checkpoint test", ["tracked.txt"])
    before_branch = git(git_repo, "branch", "--show-current")
    before_head = git(git_repo, "rev-parse", "HEAD")
    before_index = git(git_repo, "write-tree")
    before_status = git(git_repo, "status", "--porcelain=v1")

    ready_dir = tmp_path / "checkpoint-ready"
    ready_dir.mkdir()
    go = tmp_path / "checkpoint-go"
    script = """
import json
import sys
import time
from pathlib import Path
from rewind.store import RewindPaths
from rewind.task import record_checkpoint

root, ready, go, label = map(Path, sys.argv[1:5])
ready.write_text("ready\\n", encoding="utf-8")
while not go.exists():
    time.sleep(0.001)
result = record_checkpoint(RewindPaths(root), str(label))
print(json.dumps(result))
"""
    processes = [
        subprocess.Popen(
            [
                sys.executable,
                "-c",
                script,
                str(git_repo),
                str(ready_dir / str(index)),
                str(go),
                f"parallel-{index}",
            ],
            cwd=git_repo,
            env=_worker_env(),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        for index in range(4)
    ]
    _release_processes(processes, ready_dir, go)

    results = []
    for process in processes:
        stdout, stderr = process.communicate(timeout=30)
        assert process.returncode == 0, stderr
        results.append(json.loads(stdout))

    assert {result["checkpoint_id"] for result in results} == {
        "cp_02",
        "cp_03",
        "cp_04",
        "cp_05",
    }
    assert len({result["ref"] for result in results}) == 4
    for result in results:
        assert git(git_repo, "rev-parse", result["ref"]) == result["commit"]

    verification = verify_log(paths.events, project(paths).verify_key)
    assert verification.valid
    checkpoint_ids = [
        event["payload"]["checkpoint_id"]
        for event in verification.events
        if event["type"] == "checkpoint_recorded"
    ]
    assert len(checkpoint_ids) == len(set(checkpoint_ids)) == 5
    assert git(git_repo, "branch", "--show-current") == before_branch
    assert git(git_repo, "rev-parse", "HEAD") == before_head
    assert git(git_repo, "write-tree") == before_index
    assert git(git_repo, "status", "--porcelain=v1") == before_status


def test_parallel_event_appends_keep_sequence_and_signature_chain(git_repo: Path, tmp_path: Path) -> None:
    paths = initialize(git_repo)
    ready_dir = tmp_path / "event-ready"
    ready_dir.mkdir()
    go = tmp_path / "event-go"
    script = """
import sys
import time
from pathlib import Path
from rewind.store import RewindPaths, project

root, ready, go, artifact_id = map(Path, sys.argv[1:5])
ready.write_text("ready\\n", encoding="utf-8")
while not go.exists():
    time.sleep(0.001)
project(RewindPaths(root)).append(
    "artifact_recorded",
    {"artifact_id": str(artifact_id)},
    actor="parallel-worker",
)
"""
    processes = [
        subprocess.Popen(
            [
                sys.executable,
                "-c",
                script,
                str(git_repo),
                str(ready_dir / str(index)),
                str(go),
                f"artifact-{index}",
            ],
            cwd=git_repo,
            env=_worker_env(),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        for index in range(8)
    ]
    _release_processes(processes, ready_dir, go)

    for process in processes:
        _, stderr = process.communicate(timeout=30)
        assert process.returncode == 0, stderr

    verification = verify_log(paths.events, project(paths).verify_key)
    assert verification.valid
    assert [event["sequence"] for event in verification.events] == list(range(1, 11))
    assert len({event["content_id"] for event in verification.events}) == 10


def test_failed_initial_checkpoint_does_not_activate_or_record_task(
    git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = initialize(git_repo)
    before = events(paths)

    def fail_checkpoint(*args: object, **kwargs: object) -> object:
        raise RewindError("simulated Git checkpoint failure")

    monkeypatch.setattr(task_module, "git_checkpoint", fail_checkpoint)

    with pytest.raises(RewindError, match="simulated Git checkpoint failure"):
        start_task(paths, "Should remain inactive", ["tracked.txt"])

    assert status(paths) == {"active": False, "last_task_id": None}
    assert events(paths) == before
    assert git(git_repo, "for-each-ref", "--format=%(refname)", "refs/rewind/checkpoints") == ""


def test_finish_waits_for_in_flight_check_and_evidence_precedes_finish(
    git_repo: Path,
    tmp_path: Path,
) -> None:
    paths = initialize(git_repo)
    task = start_task(paths, "Check and finish race", ["tracked.txt"])
    command_started = tmp_path / "command-started"
    release_command = tmp_path / "release-command"
    finish_attempted = tmp_path / "finish-attempted"

    check_worker = """
import json
import sys
from pathlib import Path
from rewind.store import RewindPaths
from rewind.task import run_check

root, started, release = map(Path, sys.argv[1:4])
command = [
    sys.executable,
    "-c",
    "import sys, time; from pathlib import Path; "
    "started, release = map(Path, sys.argv[1:3]); "
    "started.write_text('started\\\\n', encoding='utf-8'); "
    "\\nwhile not release.exists(): time.sleep(0.001)",
    str(started),
    str(release),
]
print(json.dumps(run_check(RewindPaths(root), command)))
"""
    finish_worker = """
import json
import sys
from pathlib import Path
from rewind.store import RewindPaths
from rewind.task import finish_task

root, attempted = map(Path, sys.argv[1:3])
attempted.write_text("attempted\\n", encoding="utf-8")
print(json.dumps(finish_task(RewindPaths(root))))
"""

    check_process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            check_worker,
            str(git_repo),
            str(command_started),
            str(release_command),
        ],
        cwd=git_repo,
        env=_worker_env(),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    deadline = time.monotonic() + 15
    while not command_started.exists():
        if check_process.poll() is not None:
            stdout, stderr = check_process.communicate()
            raise AssertionError(f"check exited before entering command: {stdout}\n{stderr}")
        if time.monotonic() >= deadline:
            check_process.kill()
            raise AssertionError("recorded command did not start")
        time.sleep(0.01)

    finish_process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            finish_worker,
            str(git_repo),
            str(finish_attempted),
        ],
        cwd=git_repo,
        env=_worker_env(),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    deadline = time.monotonic() + 15
    while not finish_attempted.exists():
        if finish_process.poll() is not None:
            stdout, stderr = finish_process.communicate()
            check_process.kill()
            raise AssertionError(f"finish exited before attempting lifecycle lock: {stdout}\n{stderr}")
        if time.monotonic() >= deadline:
            check_process.kill()
            finish_process.kill()
            raise AssertionError("finish worker did not start")
        time.sleep(0.01)

    # The worker has entered finish_task, but the in-flight check still owns
    # the lifecycle lock. Finishing must remain blocked until evidence exists.
    time.sleep(0.1)
    assert finish_process.poll() is None

    release_command.write_text("release\n", encoding="utf-8")
    check_stdout, check_stderr = check_process.communicate(timeout=30)
    finish_stdout, finish_stderr = finish_process.communicate(timeout=30)
    assert check_process.returncode == 0, check_stderr
    assert finish_process.returncode == 0, finish_stderr
    assert json.loads(check_stdout)["passed"]
    assert json.loads(finish_stdout)["task_id"] == task["task_id"]

    relevant = [
        event
        for event in events(paths)
        if event["payload"].get("task_id") == task["task_id"]
    ]
    evidence_positions = [
        index for index, event in enumerate(relevant) if event["type"] == "command_evidence_recorded"
    ]
    finish_positions = [index for index, event in enumerate(relevant) if event["type"] == "task_finished"]
    assert len(evidence_positions) == len(finish_positions) == 1
    assert evidence_positions[0] < finish_positions[0]
    assert not any(
        event["type"] == "command_evidence_recorded"
        for event in relevant[finish_positions[0] + 1 :]
    )
    assert status(paths)["active"] is False


def test_nested_task_mutation_inside_recorded_check_fails_without_deadlock(
    git_repo: Path,
) -> None:
    paths = initialize(git_repo)
    start_task(paths, "Reject nested lifecycle mutation", ["tracked.txt"])
    worker = """
import json
import sys
from pathlib import Path
from rewind.store import RewindPaths
from rewind.task import run_check

root = Path(sys.argv[1])
nested = (
    "import sys; from pathlib import Path; "
    "from rewind.store import RewindPaths; "
    "from rewind.task import record_checkpoint; "
    "record_checkpoint(RewindPaths(Path(sys.argv[1])), 'nested')"
)
result = run_check(
    RewindPaths(root),
    [sys.executable, "-c", nested, str(root)],
)
print(json.dumps(result))
"""
    process = subprocess.run(
        [sys.executable, "-c", worker, str(git_repo)],
        cwd=git_repo,
        env=_worker_env(),
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )

    assert process.returncode == 0, process.stderr
    result = json.loads(process.stdout)
    assert result["passed"] is False
    assert result["exit_code"] != 0
    assert "cannot run Rewind task lifecycle commands" in process.stderr
