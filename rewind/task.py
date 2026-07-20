"""Task lifecycle shared by CLI and MCP."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from .evidence import run_argv
from .events import verify_log
from .git_state import GitCheckpoint, checkpoint as git_checkpoint
from .store import RewindError, RewindPaths, atomic_json, project, read_json


def _state(paths: RewindPaths) -> dict[str, Any]:
    state = read_json(paths.state)
    if not isinstance(state, dict):
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
    count = sum(event["type"] == "checkpoint_recorded" for event in task_events(paths, task_id))
    return f"cp_{count + 1:02d}"


def record_checkpoint(paths: RewindPaths, label: str, kind: str = "manual") -> dict[str, Any]:
    task_id = current_task_id(paths)
    checkpoint_id = _next_checkpoint_id(paths, task_id)
    snapshot: GitCheckpoint = git_checkpoint(paths.root, task_id, checkpoint_id, label)
    payload = {
        "task_id": task_id,
        "checkpoint_id": checkpoint_id,
        "label": label,
        "kind": kind,
        "commit": snapshot.commit,
        "tree": snapshot.tree,
        "ref": snapshot.ref,
        "head": snapshot.head,
    }
    project(paths).append("checkpoint_recorded", payload)
    return payload


def start_task(paths: RewindPaths, intent: str, allowed_paths: list[str]) -> dict[str, Any]:
    state = _state(paths)
    if state.get("current_task_id"):
        raise RewindError(f"Task {state['current_task_id']} is already active.")
    if not intent.strip():
        raise RewindError("Task intent cannot be empty.")
    if not allowed_paths:
        raise RewindError("Declare at least one narrow path with `--allow`.")
    task_id = f"task_{uuid.uuid4().hex[:10]}"
    project(paths).append(
        "task_started",
        {
            "task_id": task_id,
            "intent": intent.strip(),
            "allowed_paths": allowed_paths,
        },
        actor="human",
    )
    atomic_json(paths.state, {**state, "current_task_id": task_id})
    checkpoint = record_checkpoint(paths, "Task started", kind="start")
    return {"task_id": task_id, "intent": intent.strip(), "checkpoint": checkpoint}


def run_check(paths: RewindPaths, argv: list[str]) -> dict[str, Any]:
    task_id = current_task_id(paths)
    bound = record_checkpoint(paths, f"Before check: {' '.join(argv)}", kind="pre_check")
    result = run_argv(paths, argv)
    payload = {
        "task_id": task_id,
        "checkpoint_id": bound["checkpoint_id"],
        "checkpoint_commit": bound["commit"],
        "tree": bound["tree"],
        **result,
    }
    event = project(paths).append("command_evidence_recorded", payload)
    return {**payload, "event_id": event["content_id"]}


def finish_task(paths: RewindPaths) -> dict[str, Any]:
    state = _state(paths)
    task_id = current_task_id(paths)
    final_checkpoint = record_checkpoint(paths, "Task finished", kind="finish")
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

