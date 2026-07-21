"""Task lifecycle shared by CLI and MCP."""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any

from .evidence import RECORDED_CHECK_ROOT_ENV, run_argv
from .events import file_lock, verify_log
from .git_state import GitCheckpoint, checkpoint as git_checkpoint
from .store import RewindError, RewindPaths, atomic_json, project, read_json


def _state(paths: RewindPaths) -> dict[str, Any]:
    try:
        state = read_json(paths.state)
    except (OSError, json.JSONDecodeError) as exc:
        raise RewindError("Mutable task state is missing or invalid.") from exc
    if not isinstance(state, dict):
        if not paths.local.exists() or not paths.state.exists():
            raise RewindError("Rewind is not initialized. Run `rewind init` first.")
        raise RewindError("Mutable task state is missing or invalid.")
    return state


def events(paths: RewindPaths) -> list[dict[str, Any]]:
    log = project(paths)
    verification = verify_log(paths.events, log.verify_key)
    if not verification.valid:
        first = verification.issues[0]
        raise RewindError(f"Signed log verification failed ({first.code}): {first.message}")
    return verification.events


def current_task_id(paths: RewindPaths) -> str:
    task_id = _state(paths).get("current_task_id")
    if not task_id:
        raise RewindError("No Rewind task is active. Run `rewind start` first.")
    return str(task_id)


def task_events(paths: RewindPaths, task_id: str) -> list[dict[str, Any]]:
    return [event for event in events(paths) if event["payload"].get("task_id") == task_id]


def find_task(paths: RewindPaths, task_id: str | None = None) -> str:
    if task_id:
        known = {
            str(event["payload"]["task_id"])
            for event in events(paths)
            if event["type"] == "task_started"
        }
        if task_id not in known:
            raise RewindError(f"Unknown task ID: {task_id}")
        return task_id
    active = _state(paths).get("current_task_id")
    if active:
        return str(active)
    starts = [event for event in events(paths) if event["type"] == "task_started"]
    if not starts:
        raise RewindError("No Rewind task has been recorded.")
    return str(starts[-1]["payload"]["task_id"])


def _next_checkpoint_id(paths: RewindPaths, task_id: str) -> str:
    numbers: list[int] = []
    for event in task_events(paths, task_id):
        if event["type"] != "checkpoint_recorded":
            continue
        checkpoint_id = str(event["payload"].get("checkpoint_id", ""))
        prefix, separator, suffix = checkpoint_id.partition("_")
        if prefix == "cp" and separator and suffix.isdigit():
            numbers.append(int(suffix))
    return f"cp_{max(numbers, default=0) + 1:02d}"


def _task_lock_path(paths: RewindPaths) -> Path:
    return paths.local / "tmp" / "task.lock"


def _refuse_nested_task_mutation(paths: RewindPaths) -> None:
    recorded_root = os.environ.get(RECORDED_CHECK_ROOT_ENV)
    if recorded_root and Path(recorded_root).resolve() == paths.root.resolve():
        raise RewindError(
            "A recorded check cannot run Rewind task lifecycle commands in the same "
            "repository. Run the nested operation before or after `rewind run`."
        )


def _snapshot_checkpoint(
    paths: RewindPaths,
    task_id: str,
    checkpoint_id: str,
    label: str,
    kind: str,
) -> dict[str, Any]:
    """Create the private Git snapshot without appending lifecycle state."""
    snapshot: GitCheckpoint = git_checkpoint(paths.root, task_id, checkpoint_id, label)
    return {
        "task_id": task_id,
        "checkpoint_id": checkpoint_id,
        "label": label,
        "kind": kind,
        "commit": snapshot.commit,
        "tree": snapshot.tree,
        "ref": snapshot.ref,
        "head": snapshot.head,
    }


def _record_checkpoint_locked(
    paths: RewindPaths,
    task_id: str,
    label: str,
    kind: str,
) -> dict[str, Any]:
    checkpoint_id = _next_checkpoint_id(paths, task_id)
    payload = _snapshot_checkpoint(paths, task_id, checkpoint_id, label, kind)
    project(paths).append("checkpoint_recorded", payload)
    return payload


def record_checkpoint(paths: RewindPaths, label: str, kind: str = "manual") -> dict[str, Any]:
    _refuse_nested_task_mutation(paths)
    _state(paths)
    with file_lock(_task_lock_path(paths)):
        task_id = current_task_id(paths)
        return _record_checkpoint_locked(paths, task_id, label, kind)


def start_task(paths: RewindPaths, intent: str, allowed_paths: list[str]) -> dict[str, Any]:
    if not intent.strip():
        raise RewindError("Task intent cannot be empty.")
    if not allowed_paths:
        raise RewindError("Declare at least one narrow path with `--allow`.")
    _refuse_nested_task_mutation(paths)
    _state(paths)
    with file_lock(_task_lock_path(paths)):
        state = _state(paths)
        if state.get("current_task_id"):
            raise RewindError(f"Task {state['current_task_id']} is already active.")
        task_id = f"task_{uuid.uuid4().hex[:10]}"
        config = read_json(paths.config, {})
        protected_globs = config.get("protected_globs", []) if isinstance(config, dict) else []
        if not isinstance(protected_globs, list) or not all(
            isinstance(pattern, str) for pattern in protected_globs
        ):
            raise RewindError("Rewind config `protected_globs` must be a list of strings.")

        # Snapshot first. A Git failure therefore cannot strand current_task_id
        # in an active state or record a task that never had an initial state.
        checkpoint = _snapshot_checkpoint(
            paths,
            task_id,
            "cp_01",
            "Task started",
            "start",
        )
        project(paths).append(
            "task_started",
            {
                "task_id": task_id,
                "intent": intent.strip(),
                "allowed_paths": allowed_paths,
                "protected_globs": protected_globs,
            },
            actor="human",
        )
        project(paths).append("checkpoint_recorded", checkpoint)
        atomic_json(paths.state, {**state, "current_task_id": task_id})
        return {"task_id": task_id, "intent": intent.strip(), "checkpoint": checkpoint}


def run_check(
    paths: RewindPaths,
    argv: list[str],
    action_id: str | None = None,
) -> dict[str, Any]:
    if not argv:
        raise RewindError("No command supplied. Use `rewind run -- COMMAND [ARGS...]`.")
    if action_id is not None and not action_id.strip():
        raise RewindError("Action ID cannot be empty.")
    _refuse_nested_task_mutation(paths)
    _state(paths)
    with file_lock(_task_lock_path(paths)):
        task_id = current_task_id(paths)
        bound = _record_checkpoint_locked(
            paths,
            task_id,
            f"Before check: {' '.join(argv)}",
            kind="pre_check",
        )
        result = run_argv(paths, argv)
        payload = {
            "task_id": task_id,
            "checkpoint_id": bound["checkpoint_id"],
            "checkpoint_commit": bound["commit"],
            "tree": bound["tree"],
            **result,
        }
        if action_id is not None:
            payload["action_id"] = action_id.strip()
        event = project(paths).append("command_evidence_recorded", payload)
        return {**payload, "event_id": event["content_id"]}


def finish_task(paths: RewindPaths) -> dict[str, Any]:
    _refuse_nested_task_mutation(paths)
    _state(paths)
    with file_lock(_task_lock_path(paths)):
        state = _state(paths)
        task_id = current_task_id(paths)
        final_checkpoint = _record_checkpoint_locked(paths, task_id, "Task finished", kind="finish")
        project(paths).append(
            "task_finished",
            {
                "task_id": task_id,
                "final_checkpoint_id": final_checkpoint["checkpoint_id"],
                "final_commit": final_checkpoint["commit"],
                "final_tree": final_checkpoint["tree"],
            },
        )
        atomic_json(paths.state, {**state, "current_task_id": None, "last_task_id": task_id})
        return {"task_id": task_id, "checkpoint": final_checkpoint}


def status(paths: RewindPaths) -> dict[str, Any]:
    state = _state(paths)
    active = state.get("current_task_id")
    if not active:
        return {"active": False, "last_task_id": state.get("last_task_id")}
    relevant = task_events(paths, str(active))
    start = next(event for event in relevant if event["type"] == "task_started")
    checkpoints = [event for event in relevant if event["type"] == "checkpoint_recorded"]
    checks = [event for event in relevant if event["type"] == "command_evidence_recorded"]
    return {
        "active": True,
        "task_id": active,
        "intent": start["payload"]["intent"],
        "allowed_paths": start["payload"]["allowed_paths"],
        "checkpoint_count": len(checkpoints),
        "check_count": len(checks),
        "last_checkpoint": checkpoints[-1]["payload"] if checkpoints else None,
    }
