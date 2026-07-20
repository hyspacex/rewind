from pathlib import Path

from nacl.signing import SigningKey

from rewind.crypto import canonical_bytes
from rewind.events import EventLog, verify_log


def test_canonical_json_is_stable() -> None:
    assert canonical_bytes({"b": 2, "a": "é"}) == b'{"a":"\xc3\xa9","b":2}'


def test_signed_log_detects_mutation_and_chain_break(tmp_path: Path) -> None:
    key = SigningKey.generate()
    path = tmp_path / "events.jsonl"
    log = EventLog(path, key)
    log.append("recorder_initialized", {"name": "test"})
    log.append("task_started", {"task_id": "task_01"})
    assert verify_log(path, key.verify_key).valid

    original = path.read_text()
    path.write_text(original.replace("task_01", "task_02"))
    verification = verify_log(path, key.verify_key)
    assert not verification.valid
    assert any(issue.code == "bad_signature" for issue in verification.issues)

    path.write_text(original)
    lines = path.read_text().splitlines()
    path.write_text(lines[1] + "\n")
    verification = verify_log(path, key.verify_key)
    assert not verification.valid
    assert any(issue.code in {"sequence_break", "chain_break"} for issue in verification.issues)
