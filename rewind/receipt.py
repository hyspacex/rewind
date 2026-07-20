"""Deterministic task receipt shared by terminal and HTML renderers."""

from __future__ import annotations

import fnmatch
import json
import re
from datetime import datetime
from pathlib import PurePosixPath
from typing import Any

from .crypto import key_id, sha256_bytes
from .events import verify_log
from .git_state import changed_files
from .store import RewindError, RewindPaths, project, read_json
from .task import find_task, task_events

DEPENDENCY_PATTERNS = (
    "pyproject.toml",
    "requirements*.txt",
    "poetry.lock",
    "package.json",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "Cargo.toml",
    "Cargo.lock",
    "go.mod",
    "go.sum",
)


def path_matches(path: str, pattern: str) -> bool:
    normalized = path.replace("\\", "/").lstrip("./")
    candidate = PurePosixPath(normalized)
    if pattern.endswith("/**") and normalized.startswith(pattern[:-3].rstrip("/") + "/"):
        return True
    return fnmatch.fnmatchcase(normalized, pattern) or candidate.match(pattern)


def matches_any(path: str, patterns: list[str] | tuple[str, ...]) -> bool:
    return any(path_matches(path, pattern) for pattern in patterns)


def dependency_files(files: list[str]) -> list[str]:
    return [path for path in files if matches_any(PurePosixPath(path).name, DEPENDENCY_PATTERNS)]


def _elapsed(started_at: str, ended_at: str) -> tuple[int, str]:
    start = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
    end = datetime.fromisoformat(ended_at.replace("Z", "+00:00"))
    seconds = max(0, round((end - start).total_seconds()))
    if seconds < 60:
        return seconds, f"{seconds}s"
    minutes, remainder = divmod(seconds, 60)
    return seconds, f"{minutes}m {remainder:02d}s"


def _test_count(paths: RewindPaths, checks: list[dict[str, Any]]) -> int | None:
    total = 0
    found = False
    patterns = (
        re.compile(r"(?<!\d)(\d+)\s+passed\b"),
        re.compile(r"\bRan\s+(\d+)\s+tests?\b"),
    )
    for check in checks:
        if not check["payload"].get("passed") or check["payload"].get("kind") != "test":
            continue
        digest = check["payload"]["evidence_sha256"]
        object_path = paths.objects / digest
        if not object_path.exists():
            continue
        try:
            evidence = json.loads(object_path.read_bytes())
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        text = f"{evidence.get('stdout', '')}\n{evidence.get('stderr', '')}"
        for pattern in patterns:
            match = pattern.search(text)
            if match:
                total += int(match.group(1))
                found = True
                break
    return total if found else None


def build_receipt(paths: RewindPaths, requested_task_id: str | None = None) -> dict[str, Any]:
    task_id = find_task(paths, requested_task_id)
    log = project(paths)
    log_verification = verify_log(paths.events, log.verify_key)
    relevant = task_events(paths, task_id)
    start = next((event for event in relevant if event["type"] == "task_started"), None)
    if start is None:
        raise RewindError(f"Task start event is missing for {task_id}.")
    checkpoints = [event for event in relevant if event["type"] == "checkpoint_recorded"]
    checks = [event for event in relevant if event["type"] == "command_evidence_recorded"]
    finish = next((event for event in reversed(relevant) if event["type"] == "task_finished"), None)
    if not checkpoints:
        raise RewindError(f"Task {task_id} has no checkpoints.")

    initial = checkpoints[0]["payload"]
    final = (
        next(
            event["payload"]
            for event in checkpoints
            if finish and event["payload"]["checkpoint_id"] == finish["payload"]["final_checkpoint_id"]
        )
        if finish
        else checkpoints[-1]["payload"]
    )
    changed = changed_files(paths.root, initial["tree"], final["tree"])
    allowed = list(start["payload"]["allowed_paths"])
    within_scope = [path for path in changed if matches_any(path, allowed)]
    outside_scope = [path for path in changed if path not in within_scope]
    dependencies = dependency_files(changed)
    config = read_json(paths.config, {})
    protected_patterns = list(config.get("protected_globs", []))
    protected = [path for path in changed if matches_any(path, protected_patterns)]

    evidence_issues: list[dict[str, str]] = []
    for check in checks:
        digest = str(check["payload"]["evidence_sha256"])
        object_path = paths.objects / digest
        if not object_path.exists():
            evidence_issues.append({"code": "missing_evidence", "message": f"Evidence {digest[:12]} is missing."})
        elif sha256_bytes(object_path.read_bytes()) != digest:
            evidence_issues.append(
                {"code": "evidence_tampered", "message": f"Evidence {digest[:12]} failed its SHA-256 check."}
            )

    passing = [check for check in checks if check["payload"].get("passed")]
    last_passing = passing[-1] if passing else None
    last_check = checks[-1] if checks else None
    after_evidence = (
        changed_files(paths.root, last_passing["payload"]["tree"], final["tree"])
        if last_passing
        else []
    )
    recovery = None
    if last_passing:
        checkpoint_id = last_passing["payload"]["checkpoint_id"]
        recovery = next(
            event["payload"] for event in checkpoints if event["payload"]["checkpoint_id"] == checkpoint_id
        )

    signals: list[dict[str, Any]] = [
        {
            "key": "scope",
            "label": "Scope",
            "status": "WARN" if outside_scope else "PASS",
            "summary": (
                f"{len(outside_scope)} file{'s' if len(outside_scope) != 1 else ''} outside declared scope"
                if outside_scope
                else "All changed files are within declared scope"
            ),
            "files": outside_scope,
        },
        {
            "key": "evidence",
            "label": "Evidence",
            "status": (
                "FAIL"
                if evidence_issues or not passing or (last_check and not last_check["payload"].get("passed"))
                else "WARN"
                if after_evidence
                else "PASS"
            ),
            "summary": (
                evidence_issues[0]["message"]
                if evidence_issues
                else "No successful check was recorded"
                if not passing
                else "The most recent recorded check failed"
                if last_check and not last_check["payload"].get("passed")
                else f"{len(after_evidence)} file{'s' if len(after_evidence) != 1 else ''} changed after the last passing check"
                if after_evidence
                else "Final state matches the last passing check"
            ),
            "files": after_evidence,
        },
        {
            "key": "dependencies",
            "label": "Dependencies / protected",
            "status": "WARN" if dependencies or protected else "PASS",
            "summary": (
                f"{len(dependencies)} dependency and {len(protected)} protected-path changes"
                if dependencies or protected
                else "No dependency or protected-path changes"
            ),
            "files": sorted(set(dependencies + protected)),
        },
        {
            "key": "recovery",
            "label": "Recovery",
            "status": "READY" if recovery else "NONE",
            "summary": (
                f"{recovery['checkpoint_id']} is the last tested state"
                if recovery
                else "No passing-evidence checkpoint is available"
            ),
            "files": [],
        },
    ]
    review_items = sum(signal["status"] in {"WARN", "FAIL"} for signal in signals)
    if not log_verification.valid or evidence_issues or not passing:
        outcome = "Needs attention"
        outcome_key = "fail"
    elif review_items:
        outcome = "Completed with items to review"
        outcome_key = "warn"
    else:
        outcome = "Completed"
        outcome_key = "pass"

    ended_at = (finish or checkpoints[-1])["recorded_at"]
    elapsed_seconds, elapsed_display = _elapsed(start["recorded_at"], ended_at)
    timeline = [
        {
            "sequence": event["sequence"],
            "recorded_at": event["recorded_at"],
            "type": event["type"],
            "actor": event["actor"],
            "content_id": event["content_id"],
            "payload": event["payload"],
        }
        for event in relevant
    ]
    return {
        "schema": "rewind.receipt.v1",
        "task_id": task_id,
        "intent": start["payload"]["intent"],
        "allowed_paths": allowed,
        "started_at": start["recorded_at"],
        "ended_at": ended_at,
        "elapsed_seconds": elapsed_seconds,
        "elapsed_display": elapsed_display,
        "finished": finish is not None,
        "outcome": outcome,
        "outcome_key": outcome_key,
        "review_item_count": review_items,
        "signals": signals,
        "changed_files": changed,
        "within_scope": within_scope,
        "outside_scope": outside_scope,
        "dependency_files": dependencies,
        "protected_files": protected,
        "changed_after_passing_evidence": after_evidence,
        "checks": [event["payload"] for event in checks],
        "passing_check_count": len(passing),
        "test_count": _test_count(paths, checks),
        "initial_checkpoint": initial,
        "final_checkpoint": final,
        "recommended_recovery": recovery,
        "timeline": timeline,
        "audit": {
            "log_valid": log_verification.valid,
            "event_count": len(log_verification.events),
            "issues": [
                {
                    "level": issue.level,
                    "code": issue.code,
                    "message": issue.message,
                    "sequence": issue.sequence,
                }
                for issue in log_verification.issues
            ]
            + evidence_issues,
            "key_id": key_id(log.verify_key),
            "threat_model": "Tamper-evident assertions under one trusted local recorder key.",
        },
    }
