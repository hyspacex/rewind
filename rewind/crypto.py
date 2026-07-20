"""Canonicalization, hashing, and Ed25519 signing primitives."""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from typing import Any

from nacl.exceptions import BadSignatureError
from nacl.signing import SigningKey, VerifyKey


def canonical_bytes(value: Any) -> bytes:
    """Return the one unambiguous JSON representation Rewind signs."""
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def content_id(value: Any) -> str:
    return sha256_bytes(canonical_bytes(value))


def encode_key(key: bytes) -> str:
    return base64.b64encode(key).decode("ascii")


def decode_key(value: str) -> bytes:
    return base64.b64decode(value.encode("ascii"), validate=True)


def key_id(verify_key: VerifyKey) -> str:
    return sha256_bytes(bytes(verify_key))[:16]


def generate_keypair(private_path: Path, public_path: Path) -> VerifyKey:
    private_path.parent.mkdir(parents=True, exist_ok=True)
    signing_key = SigningKey.generate()
    private_path.write_text(encode_key(bytes(signing_key)) + "\n", encoding="utf-8")
    private_path.chmod(0o600)
    public_path.write_text(encode_key(bytes(signing_key.verify_key)) + "\n", encoding="utf-8")
    return signing_key.verify_key


def load_signing_key(path: Path) -> SigningKey:
    return SigningKey(decode_key(path.read_text(encoding="utf-8").strip()))


def load_verify_key(path: Path) -> VerifyKey:
    return VerifyKey(decode_key(path.read_text(encoding="utf-8").strip()))


def sign_envelope(signing_key: SigningKey, unsigned_event: dict[str, Any]) -> tuple[str, str]:
    event_id = content_id(unsigned_event)
    envelope = {"content_id": event_id, "event": unsigned_event}
    signature = signing_key.sign(canonical_bytes(envelope)).signature
    return event_id, encode_key(signature)


def verify_envelope(
    verify_key: VerifyKey,
    unsigned_event: dict[str, Any],
    event_id: str,
    signature: str,
) -> bool:
    if content_id(unsigned_event) != event_id:
        return False
    envelope = {"content_id": event_id, "event": unsigned_event}
    try:
        verify_key.verify(canonical_bytes(envelope), decode_key(signature))
    except (BadSignatureError, ValueError):
        return False
    return True

