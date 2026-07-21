"""Small public lifecycle helpers for historical replay."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .events import file_lock
from .store import RewindError, RewindPaths, packaged_policy, project, put_object
from .task import events


def _audit_lock_path(paths: RewindPaths) -> Path:
    return paths.local / "tmp" / "audit.lock"


def activate_policy(paths: RewindPaths, version: str) -> dict[str, Any]:
    if version not in {"v1", "v2"}:
        raise RewindError("Policy version must be `v1` or `v2`.")
    with file_lock(_audit_lock_path(paths)):
        policy_hash = put_object(paths, packaged_policy(version))
        return project(paths).append(
            "policy_activated",
            {"policy_id": version, "policy_object_sha256": policy_hash},
            actor="human",
        )


def change_role(
    paths: RewindPaths,
    subject: str,
    role: str,
    *,
    grant: bool,
) -> dict[str, Any]:
    if not subject.strip() or not role.strip():
        raise RewindError("Role subject and role name cannot be empty.")
    with file_lock(_audit_lock_path(paths)):
        return project(paths).append(
            "role_granted" if grant else "role_revoked",
            {"subject": subject.strip(), "role": role.strip()},
            actor="human",
        )


def record_artifact(
    paths: RewindPaths,
    action_id: str,
    artifact: Path,
    proposer: str,
) -> dict[str, Any]:
    action_id = action_id.strip()
    proposer = proposer.strip()
    if not action_id or not proposer:
        raise RewindError("Action ID and proposer cannot be empty.")
    artifact = artifact.expanduser().resolve()
    if not artifact.is_file():
        raise RewindError(f"Artifact file does not exist: {artifact}")
    with file_lock(_audit_lock_path(paths)):
        lifecycle = events(paths)
        matching_checks = [
            event
            for event in lifecycle
            if event["type"] == "command_evidence_recorded"
            and event["payload"].get("action_id") == action_id
            and event["payload"].get("passed")
        ]
        if not matching_checks:
            raise RewindError(
                f"Action {action_id} has no passing evidence. "
                f"Run `rewind run --action {action_id} -- COMMAND` first."
            )
        if any(
            event["type"] == "artifact_recorded"
            and event["payload"].get("action_id") == action_id
            for event in lifecycle
        ):
            raise RewindError(f"Action {action_id} already has a recorded artifact.")
        check = matching_checks[-1]
        artifact_hash = put_object(paths, artifact.read_bytes())
        return project(paths).append(
            "artifact_recorded",
            {
                "action_id": action_id,
                "tree": check["payload"]["tree"],
                "artifact_sha256": artifact_hash,
                "proposer": proposer,
                "protected_change": True,
            },
            actor=proposer,
        )


def approve_action(
    paths: RewindPaths,
    action_id: str,
    approver: str = "human",
) -> dict[str, Any]:
    action_id = action_id.strip()
    approver = approver.strip()
    if not action_id or not approver:
        raise RewindError("Action ID and approver cannot be empty.")
    with file_lock(_audit_lock_path(paths)):
        lifecycle = events(paths)
        artifact = next(
            (
                event
                for event in reversed(lifecycle)
                if event["type"] == "artifact_recorded"
                and event["payload"].get("action_id") == action_id
            ),
            None,
        )
        if artifact is None:
            raise RewindError(f"Action {action_id} has no recorded artifact.")
        if any(
            event["type"] == "deployment_observed"
            and event["payload"].get("action_id") == action_id
            for event in lifecycle
        ):
            raise RewindError(f"Action {action_id} already has an observed deployment.")
        payload = artifact["payload"]
        return project(paths).append(
            "approval_recorded",
            {
                "action_id": action_id,
                "tree": payload["tree"],
                "artifact_sha256": payload["artifact_sha256"],
                "approver": approver,
            },
            actor=approver,
        )


def observe_deployment(
    paths: RewindPaths,
    action_id: str,
    actor: str,
) -> dict[str, Any]:
    action_id = action_id.strip()
    actor = actor.strip()
    if not action_id or not actor:
        raise RewindError("Action ID and deployment actor cannot be empty.")
    with file_lock(_audit_lock_path(paths)):
        lifecycle = events(paths)
        artifact = next(
            (
                event
                for event in reversed(lifecycle)
                if event["type"] == "artifact_recorded"
                and event["payload"].get("action_id") == action_id
            ),
            None,
        )
        if artifact is None:
            raise RewindError(f"Action {action_id} has no recorded artifact.")
        if any(
            event["type"] == "deployment_observed"
            and event["payload"].get("action_id") == action_id
            for event in lifecycle
        ):
            raise RewindError(f"Action {action_id} already has an observed deployment.")
        payload = artifact["payload"]
        return project(paths).append(
            "deployment_observed",
            {
                "action_id": action_id,
                "tree": payload["tree"],
                "artifact_sha256": payload["artifact_sha256"],
                "proposer": payload.get("proposer"),
                "actor": actor,
            },
            actor="observer",
        )
