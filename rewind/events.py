"""Signed append-only JSONL event records."""

from __future__ import annotations

import fcntl
import json
import os
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

from nacl.signing import SigningKey, VerifyKey

from .crypto import canonical_bytes, key_id, sign_envelope, verify_envelope

EVENT_SCHEMA = "rewind.event.v1"
EVENT_TYPES = {
    "recorder_initialized",
    "task_started",
    "checkpoint_recorded",
    "command_evidence_recorded",
    "task_finished",
    "policy_activated",
    "role_granted",
    "role_revoked",
    "approval_recorded",
    "artifact_recorded",
    "deployment_observed",
}


class IntegrityError(RuntimeError):
    """Raised when a signed log cannot be trusted."""


@dataclass(frozen=True)
class VerificationIssue:
    level: str
    code: str
    message: str
    sequence: int | None = None


@dataclass
class LogVerification:
    valid: bool
    events: list[dict[str, Any]] = field(default_factory=list)
    issues: list[VerificationIssue] = field(default_factory=list)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def unsigned_part(event: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in event.items() if key not in {"content_id", "signature"}}


def parse_jsonl(path: Path) -> Iterable[tuple[int, dict[str, Any]]]:
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise IntegrityError(f"invalid JSON on line {line_number}: {exc}") from exc
            if not isinstance(value, dict):
                raise IntegrityError(f"event on line {line_number} is not an object")
            yield line_number, value


@contextmanager
def file_lock(path: Path, *, shared: bool = False) -> Iterator[None]:
    """Hold a POSIX advisory lock for a repository-local critical section."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        operation = fcntl.LOCK_SH if shared else fcntl.LOCK_EX
        fcntl.flock(handle.fileno(), operation)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _event_lock_path(path: Path) -> Path:
    return path.parent / "tmp" / "events.lock"


def _verify_log_unlocked(path: Path, verify_key: VerifyKey) -> LogVerification:
    result = LogVerification(valid=True)
    expected_previous: str | None = None
    expected_sequence = 1
    try:
        rows = list(parse_jsonl(path))
    except IntegrityError as exc:
        return LogVerification(
            valid=False,
            issues=[VerificationIssue("L0", "invalid_json", str(exc))],
        )

    for line_number, event in rows:
        sequence = event.get("sequence")
        required = {
            "schema",
            "sequence",
            "recorded_at",
            "type",
            "actor",
            "payload",
            "previous_content_id",
            "key_id",
            "content_id",
            "signature",
        }
        missing = sorted(required - event.keys())
        if missing:
            result.issues.append(
                VerificationIssue("L0", "missing_fields", f"line {line_number}: missing {missing}", sequence)
            )
            continue
        if event["schema"] != EVENT_SCHEMA or event["type"] not in EVENT_TYPES:
            result.issues.append(
                VerificationIssue("L0", "invalid_structure", f"line {line_number}: invalid schema or type", sequence)
            )
        if sequence != expected_sequence:
            result.issues.append(
                VerificationIssue(
                    "L0",
                    "sequence_break",
                    f"line {line_number}: expected sequence {expected_sequence}, found {sequence}",
                    sequence,
                )
            )
        if event["previous_content_id"] != expected_previous:
            result.issues.append(
                VerificationIssue(
                    "L1",
                    "chain_break",
                    f"line {line_number}: previous content ID does not match",
                    sequence,
                )
            )
        if event["key_id"] != key_id(verify_key):
            result.issues.append(
                VerificationIssue("L1", "wrong_key", f"line {line_number}: recorder key does not match", sequence)
            )
        if not verify_envelope(
            verify_key,
            unsigned_part(event),
            str(event["content_id"]),
            str(event["signature"]),
        ):
            result.issues.append(
                VerificationIssue("L1", "bad_signature", f"line {line_number}: signature or content ID failed", sequence)
            )
        expected_previous = str(event["content_id"])
        expected_sequence += 1
        result.events.append(event)

    result.valid = not result.issues
    return result


def verify_log(path: Path, verify_key: VerifyKey) -> LogVerification:
    # Readers participate in the same lock as appenders so they never observe
    # a partially flushed JSONL record.
    with file_lock(_event_lock_path(path), shared=True):
        return _verify_log_unlocked(path, verify_key)


class EventLog:
    def __init__(self, path: Path, signing_key: SigningKey):
        self.path = path
        self.signing_key = signing_key
        self.verify_key = signing_key.verify_key

    def append(self, event_type: str, payload: dict[str, Any], actor: str = "recorder") -> dict[str, Any]:
        if event_type not in EVENT_TYPES:
            raise ValueError(f"unsupported event type: {event_type}")
        with file_lock(_event_lock_path(self.path)):
            verification = _verify_log_unlocked(self.path, self.verify_key)
            if not verification.valid:
                raise IntegrityError("refusing to append to an invalid event log")
            previous = verification.events[-1]["content_id"] if verification.events else None
            unsigned = {
                "schema": EVENT_SCHEMA,
                "sequence": len(verification.events) + 1,
                "recorded_at": utc_now(),
                "type": event_type,
                "actor": actor,
                "payload": payload,
                "previous_content_id": previous,
                "key_id": key_id(self.verify_key),
            }
            event_id, signature = sign_envelope(self.signing_key, unsigned)
            event = {**unsigned, "content_id": event_id, "signature": signature}
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("ab") as handle:
                handle.write(canonical_bytes(event) + b"\n")
                handle.flush()
                os.fsync(handle.fileno())
            return event
