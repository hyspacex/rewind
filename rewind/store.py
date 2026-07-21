"""Repository-local paths and immutable object storage."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

from .crypto import encode_key, generate_keypair, key_id, load_signing_key, sha256_bytes
from .events import EventLog


class RewindError(RuntimeError):
    """A safe, user-facing Rewind failure."""


@dataclass(frozen=True)
class RewindPaths:
    root: Path

    @property
    def local(self) -> Path:
        return self.root / ".rewind"

    @property
    def config(self) -> Path:
        return self.local / "config.json"

    @property
    def events(self) -> Path:
        return self.local / "events.jsonl"

    @property
    def state(self) -> Path:
        return self.local / "state.json"

    @property
    def private_key(self) -> Path:
        return self.local / "keys" / "recorder.key"

    @property
    def public_key(self) -> Path:
        return self.local / "keys" / "recorder.pub"

    @property
    def objects(self) -> Path:
        return self.local / "objects" / "sha256"


DEFAULT_CONFIG = {
    "schema": "rewind.config.v1",
    "protected_globs": [
        ".github/**",
        "deploy/**",
        "deployment/**",
        "Dockerfile",
        "docker-compose*.yml",
        "docker-compose*.yaml",
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
    ],
}

SHARED_LOCAL_FILES = {".gitignore", "config.json"}
REQUIRED_LOCAL_IGNORES = ("events.jsonl", "state.json", "objects/", "keys/", "tmp/")


def git_root(cwd: Path | None = None) -> Path:
    process = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
    )
    if process.returncode:
        raise RewindError("Rewind requires an existing Git repository.")
    return Path(process.stdout.strip()).resolve()


def require_head(root: Path) -> str:
    process = subprocess.run(
        ["git", "rev-parse", "--verify", "HEAD"],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )
    if process.returncode:
        raise RewindError("Rewind requires a Git repository with at least one commit.")
    return process.stdout.strip()


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, sort_keys=True, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def put_object(paths: RewindPaths, data: bytes) -> str:
    digest = sha256_bytes(data)
    destination = paths.objects / digest
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if destination.read_bytes() != data:
            raise RewindError(f"content-addressed object mismatch for {digest}")
        return digest
    fd, temp_name = tempfile.mkstemp(prefix=f".{digest}.", dir=destination.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        # Atomic replacement means concurrent readers see either no object or
        # one complete object, never a partially written evidence blob.
        if destination.exists():
            if destination.read_bytes() != data:
                raise RewindError(f"content-addressed object mismatch for {digest}")
        else:
            os.replace(temp_name, destination)
        return digest
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def get_object(paths: RewindPaths, digest: str) -> bytes:
    data = (paths.objects / digest).read_bytes()
    if sha256_bytes(data) != digest:
        raise RewindError(f"object {digest} failed its SHA-256 check")
    return data


def project(paths: RewindPaths) -> EventLog:
    if not paths.private_key.exists():
        raise RewindError("Rewind is not initialized. Run `rewind init` first.")
    return EventLog(paths.events, load_signing_key(paths.private_key))


def packaged_policy(version: str) -> bytes:
    return resources.files("rewind").joinpath("default_policies", f"{version}.json").read_bytes()


def initialize(root: Path) -> RewindPaths:
    root = root.resolve()
    require_head(root)
    paths = RewindPaths(root)
    paths.local.mkdir(parents=True, exist_ok=True)
    existing_names = {entry.name for entry in paths.local.iterdir()}
    unexpected = sorted(existing_names - SHARED_LOCAL_FILES)
    if unexpected:
        raise RewindError(
            ".rewind contains local recorder state or unknown files; refusing to overwrite: "
            + ", ".join(unexpected)
        )

    if paths.config.exists():
        try:
            config = read_json(paths.config)
        except (OSError, json.JSONDecodeError) as exc:
            raise RewindError("Existing .rewind/config.json is not valid JSON.") from exc
        if (
            not isinstance(config, dict)
            or not isinstance(config.get("protected_globs"), list)
            or not all(isinstance(pattern, str) for pattern in config["protected_globs"])
        ):
            raise RewindError(
                "Existing .rewind/config.json must contain a `protected_globs` string list."
            )
    else:
        atomic_json(paths.config, DEFAULT_CONFIG)

    ignore_path = paths.local / ".gitignore"
    existing_ignore = ignore_path.read_text(encoding="utf-8") if ignore_path.exists() else ""
    ignore_lines = existing_ignore.splitlines()
    missing_ignores = [entry for entry in REQUIRED_LOCAL_IGNORES if entry not in ignore_lines]
    if missing_ignores:
        merged_ignore = existing_ignore
        if merged_ignore and not merged_ignore.endswith("\n"):
            merged_ignore += "\n"
        merged_ignore += "".join(f"{entry}\n" for entry in missing_ignores)
        ignore_path.write_text(merged_ignore, encoding="utf-8")

    paths.objects.mkdir(parents=True, exist_ok=True)
    verify_key = generate_keypair(paths.private_key, paths.public_key)
    atomic_json(paths.state, {"schema": "rewind.state.v1", "current_task_id": None})
    log = project(paths)
    log.append(
        "recorder_initialized",
        {
            "public_key": encode_key(bytes(verify_key)),
            "key_id": key_id(verify_key),
            "threat_model": "trusted_local_recorder",
        },
    )
    policy_bytes = packaged_policy("v1")
    policy_hash = put_object(paths, policy_bytes)
    log.append(
        "policy_activated",
        {"policy_id": "v1", "policy_object_sha256": policy_hash},
        actor="human",
    )
    return paths
