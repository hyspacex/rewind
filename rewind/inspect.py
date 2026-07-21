"""Read-only inspection of recorded changes and command evidence."""

from __future__ import annotations

import json
import subprocess
from typing import Any

from .evidence import evidence_semantic_issue
from .receipt import build_receipt
from .store import RewindError, RewindPaths, get_object
from .task import find_task, task_events


def task_diff(
    paths: RewindPaths,
    task_id: str | None = None,
    *,
    stat: bool = False,
) -> str:
    """Return the exact Git diff between a task's initial and final trees."""
    receipt = build_receipt(paths, task_id)
    old_tree = str(receipt["initial_checkpoint"]["tree"])
    new_tree = str(receipt["final_checkpoint"]["tree"])
    args = ["git", "diff", "--no-ext-diff", "--no-color"]
    if stat:
        args.append("--stat")
    else:
        args.append("--binary")
    args.extend([old_tree, new_tree])
    process = subprocess.run(
        args,
        cwd=paths.root,
        capture_output=True,
        check=False,
    )
    if process.returncode:
        message = process.stderr.decode("utf-8", errors="replace").strip()
        raise RewindError(f"Could not inspect task diff: {message}")
    return process.stdout.decode("utf-8", errors="surrogateescape")


def recorded_evidence(
    paths: RewindPaths,
    task_id: str | None = None,
    digest: str | None = None,
) -> dict[str, Any]:
    """Load and verify command output referenced by a signed task event."""
    selected = find_task(paths, task_id)
    checks = [
        event
        for event in task_events(paths, selected)
        if event["type"] == "command_evidence_recorded"
    ]
    if digest is None:
        if not checks:
            raise RewindError(f"Task {selected} has no recorded command evidence.")
        event = checks[-1]
    else:
        digest = digest.strip().lower()
        if len(digest) < 8 or any(character not in "0123456789abcdef" for character in digest):
            raise RewindError("Evidence digest must be a SHA-256 value or unique prefix of at least 8 hex characters.")
        matches = [
            check
            for check in checks
            if str(check["payload"].get("evidence_sha256", "")).startswith(digest)
        ]
        matched_digests = {
            str(check["payload"]["evidence_sha256"])
            for check in matches
        }
        if not matches:
            raise RewindError(f"Evidence {digest} is not referenced by task {selected}.")
        if len(matched_digests) > 1:
            raise RewindError(f"Evidence prefix {digest} is ambiguous in task {selected}.")
        event = matches[-1]

    evidence_digest = str(event["payload"]["evidence_sha256"])
    try:
        raw = get_object(paths, evidence_digest)
    except (OSError, RewindError) as exc:
        raise RewindError(f"Could not verify evidence {evidence_digest[:12]}: {exc}") from exc
    try:
        output = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise RewindError(f"Evidence {evidence_digest[:12]} is not valid command output.") from exc
    if not isinstance(output, dict) or output.get("schema") != "rewind.command-output.v1":
        raise RewindError(f"Evidence {evidence_digest[:12]} has an unsupported schema.")
    semantic_issue = evidence_semantic_issue(event["payload"], raw)
    if semantic_issue:
        raise RewindError(
            f"Evidence {evidence_digest[:12]} is inconsistent with its signed event: "
            f"{semantic_issue}."
        )
    return {
        "task_id": selected,
        "event_id": event["content_id"],
        "checkpoint_id": event["payload"]["checkpoint_id"],
        "tree": event["payload"]["tree"],
        "kind": event["payload"].get("kind", "check"),
        "passed": bool(event["payload"].get("passed")),
        "evidence_sha256": evidence_digest,
        "output": output,
    }
