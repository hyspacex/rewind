"""Historical policy and role replay over a verified signed log."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from nacl.signing import VerifyKey

from .crypto import decode_key, sha256_bytes
from .events import VerificationIssue, verify_log
from .policy import Policy, parse_policy


@dataclass(frozen=True)
class Stage:
    level: str
    name: str
    passed: bool
    summary: str


def load_public_key(path: Path) -> VerifyKey:
    return VerifyKey(decode_key(path.read_text(encoding="utf-8").strip()))


def _policy_at(
    events: list[dict[str, Any]],
    objects: Path,
    sequence: int | None = None,
) -> Policy | None:
    active: Policy | None = None
    for event in events:
        if sequence is not None and event["sequence"] > sequence:
            break
        if event["type"] == "policy_activated":
            digest = event["payload"]["policy_object_sha256"]
            object_path = objects / digest
            if object_path.exists() and sha256_bytes(object_path.read_bytes()) == digest:
                active = parse_policy(object_path.read_bytes())
    return active


def _roles_at(events: list[dict[str, Any]], sequence: int) -> dict[str, set[str]]:
    roles: dict[str, set[str]] = {}
    for event in events:
        if event["sequence"] > sequence:
            break
        payload = event["payload"]
        if event["type"] == "role_granted":
            roles.setdefault(str(payload["subject"]), set()).add(str(payload["role"]))
        elif event["type"] == "role_revoked":
            roles.setdefault(str(payload["subject"]), set()).discard(str(payload["role"]))
    return roles


def _evidence_integrity(
    events: list[dict[str, Any]],
    objects: Path,
) -> list[VerificationIssue]:
    issues: list[VerificationIssue] = []
    for event in events:
        if event["type"] not in {"command_evidence_recorded", "artifact_recorded", "policy_activated"}:
            continue
        for field in ("evidence_sha256", "artifact_sha256", "policy_object_sha256"):
            digest = event["payload"].get(field)
            if not digest:
                continue
            path = objects / str(digest)
            if not path.exists():
                issues.append(
                    VerificationIssue(
                        "L1",
                        "object_missing",
                        f"{field} object {str(digest)[:12]} is missing",
                        event["sequence"],
                    )
                )
            elif sha256_bytes(path.read_bytes()) != digest:
                issues.append(
                    VerificationIssue(
                        "L1",
                        "object_tampered",
                        f"{field} object {str(digest)[:12]} failed SHA-256 verification",
                        event["sequence"],
                    )
                )
    return issues


def _evaluate(
    events: list[dict[str, Any]],
    action: dict[str, Any],
    policy: Policy,
    role_sequence: int,
) -> tuple[bool, bool, list[str]]:
    payload = action["payload"]
    action_id = str(payload["action_id"])
    tree = payload.get("tree")
    artifact_hash = payload.get("artifact_sha256")
    proposer = str(payload.get("proposer") or payload.get("actor") or action["actor"])
    evidence = [
        event
        for event in events
        if event["type"] == "command_evidence_recorded"
        and event["payload"].get("action_id") == action_id
        and event["payload"].get("tree") == tree
        and event["payload"].get("passed")
    ]
    approvals = [
        event
        for event in events
        if event["type"] == "approval_recorded"
        and event["payload"].get("action_id") == action_id
        and event["payload"].get("tree") == tree
        and event["payload"].get("artifact_sha256") == artifact_hash
        and event["sequence"] <= action["sequence"]
    ]
    reasons: list[str] = []
    binding_ok = True
    if policy.require_successful_evidence and not evidence:
        binding_ok = False
        reasons.append("no passing evidence is bound to the action's repository tree")
    if policy.require_human_approval and not approvals:
        binding_ok = False
        reasons.append("no human approval is bound to the action and artifact")
    if policy.separation_of_duties and approvals:
        if all(str(approval["payload"].get("approver")) == proposer for approval in approvals):
            binding_ok = False
            reasons.append("the proposer also approved the action")

    authority_ok = True
    if action["type"] == "deployment_observed" and policy.require_deployer_role:
        actor = str(payload.get("actor") or action["actor"])
        roles = _roles_at(events, role_sequence)
        if "deployer" not in roles.get(actor, set()):
            authority_ok = False
            reasons.append(f"{actor} did not possess the deployer role at deployment time")
    return binding_ok, authority_ok, reasons


def replay_action(
    events_path: Path,
    public_key_path: Path,
    objects: Path,
    action_id: str,
) -> dict[str, Any]:
    verification = verify_log(events_path, load_public_key(public_key_path))
    l0_issues = [issue for issue in verification.issues if issue.level == "L0"]
    l1_issues = [issue for issue in verification.issues if issue.level == "L1"]
    if not l0_issues:
        l1_issues.extend(_evidence_integrity(verification.events, objects))
    action = next(
        (
            event
            for event in reversed(verification.events)
            if event["payload"].get("action_id") == action_id
            and event["type"] in {"deployment_observed", "artifact_recorded"}
        ),
        None,
    )
    historical_policy = _policy_at(
        verification.events,
        objects,
        action["sequence"] if action else None,
    )
    current_policy = _policy_at(verification.events, objects)
    structure_ok = not l0_issues and action is not None and historical_policy is not None
    integrity_ok = structure_ok and not l1_issues

    historical_binding = False
    historical_authority = False
    historical_reasons: list[str] = []
    current_binding = False
    current_authority = False
    current_reasons: list[str] = []
    if integrity_ok and action and historical_policy and current_policy:
        historical_binding, historical_authority, historical_reasons = _evaluate(
            verification.events,
            action,
            historical_policy,
            action["sequence"],
        )
        current_binding, current_authority, current_reasons = _evaluate(
            verification.events,
            action,
            current_policy,
            verification.events[-1]["sequence"],
        )

    historical_allowed = bool(integrity_ok and historical_binding and historical_authority)
    current_allowed = bool(integrity_ok and current_binding and current_authority)
    if historical_allowed and not current_allowed:
        drift = "tightening or breaking drift"
    elif not historical_allowed and current_allowed:
        drift = "relaxing drift"
    elif historical_policy and current_policy and historical_policy.policy_id == current_policy.policy_id:
        drift = "none"
    elif historical_allowed:
        drift = "unchanged allow"
    else:
        drift = "unchanged deny"

    stages = [
        Stage(
            "L0",
            "Record",
            structure_ok,
            "Schema, ordering, references, and event structure pass."
            if structure_ok
            else (l0_issues[0].message if l0_issues else "Action or active policy is missing."),
        ),
        Stage(
            "L1",
            "Integrity",
            integrity_ok,
            "Content IDs, chain, signatures, and immutable objects pass."
            if integrity_ok
            else (l1_issues[0].message if l1_issues else "Record structure must pass first."),
        ),
        Stage(
            "L2",
            "Binding",
            historical_binding if integrity_ok else False,
            "Evidence, approval, tree, and artifact are bound."
            if historical_binding
            else (historical_reasons[0] if historical_reasons else "Integrity must pass first."),
        ),
        Stage(
            "L3",
            "As-of Authority",
            historical_authority if integrity_ok and historical_binding else False,
            (
                "Integrity must pass before authority is evaluated."
                if not integrity_ok
                else "Binding must pass before authority is evaluated."
                if not historical_binding
                else "The actor held the role required at that point in history."
                if historical_authority
                else
                next(
                    (reason for reason in historical_reasons if "role" in reason),
                    "The actor lacked the required authority at that point in history.",
                )
            ),
        ),
    ]
    if not integrity_ok:
        conclusion = "The record could not be authenticated."
    elif historical_allowed:
        conclusion = "The record is authentic. The action was authorized."
    elif historical_binding and not historical_authority:
        conclusion = "The record is authentic. The deployment was not authorized."
    else:
        conclusion = "The record is authentic. The action was not authorized."
    if historical_allowed and not current_allowed:
        conclusion += " This action was valid when it happened. Current policy would reject a repeat."

    return {
        "schema": "rewind.replay.v1",
        "action_id": action_id,
        "conclusion": conclusion,
        "stages": [asdict(stage) for stage in stages],
        "historical": {
            "policy_id": historical_policy.policy_id if historical_policy else None,
            "allowed": historical_allowed,
            "reasons": historical_reasons,
        },
        "current": {
            "policy_id": current_policy.policy_id if current_policy else None,
            "allowed": current_allowed,
            "reasons": current_reasons,
        },
        "drift": drift,
    }


def render_replay_text(result: dict[str, Any]) -> str:
    lines = [result["conclusion"], ""]
    for stage in result["stages"]:
        lines.append(
            f"{stage['level']} {stage['name']}: {'PASS' if stage['passed'] else 'FAIL'} — {stage['summary']}"
        )
    lines.extend(
        [
            "",
            f"Historical ({result['historical']['policy_id']}): "
            f"{'ALLOW' if result['historical']['allowed'] else 'DENY'}",
            f"Current ({result['current']['policy_id']}): "
            f"{'ALLOW' if result['current']['allowed'] else 'DENY'}",
            f"Drift: {result['drift']}",
        ]
    )
    return "\n".join(lines)
