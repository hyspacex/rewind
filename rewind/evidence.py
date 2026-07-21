"""Recorded command execution bound to a Git checkpoint."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, BinaryIO

from .crypto import canonical_bytes
from .store import RewindError, RewindPaths, put_object

TEST_COMMANDS = {"pytest", "unittest", "tox", "nox", "cargo", "go", "npm", "pnpm", "yarn"}
RECORDED_CHECK_ROOT_ENV = "REWIND_RECORDED_CHECK_ROOT"


def classify_command(argv: list[str]) -> str:
    command = Path(argv[0]).name.lower()
    if command.startswith("python") and len(argv) >= 3 and argv[1] == "-m":
        return "test" if argv[2] in {"pytest", "unittest"} else "check"
    if command in TEST_COMMANDS:
        if command in {"cargo", "go", "npm", "pnpm", "yarn"} and "test" not in argv[1:]:
            return "check"
        return "test"
    return "check"


def evidence_semantic_issue(payload: dict[str, Any], data: bytes) -> str | None:
    """Validate that signed command fields agree with their hashed output object."""
    try:
        evidence = json.loads(data)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return "object is not valid UTF-8 JSON"
    if not isinstance(evidence, dict) or evidence.get("schema") != "rewind.command-output.v1":
        return "object has an unsupported command-output schema"

    argv = evidence.get("argv")
    if (
        not isinstance(argv, list)
        or not argv
        or not all(isinstance(argument, str) for argument in argv)
    ):
        return "object argv is not a non-empty string list"
    if payload.get("argv") != argv:
        return "signed argv does not match the command-output object"

    exit_code = evidence.get("exit_code")
    duration_ms = evidence.get("duration_ms")
    if not isinstance(exit_code, int) or isinstance(exit_code, bool):
        return "object exit_code is not an integer"
    if (
        not isinstance(duration_ms, int)
        or isinstance(duration_ms, bool)
        or duration_ms < 0
    ):
        return "object duration_ms is not a non-negative integer"
    if payload.get("exit_code") != exit_code:
        return "signed exit_code does not match the command-output object"
    if payload.get("duration_ms") != duration_ms:
        return "signed duration does not match the command-output object"
    if type(payload.get("passed")) is not bool or payload["passed"] != (exit_code == 0):
        return "signed pass/fail result contradicts the recorded exit code"
    if payload.get("kind") != classify_command(argv):
        return "signed command kind does not match the recorded argv"
    if not isinstance(evidence.get("stdout"), str) or not isinstance(evidence.get("stderr"), str):
        return "object stdout and stderr must be strings"
    return None


def _pump(stream: BinaryIO, destination: BinaryIO, chunks: list[bytes]) -> None:
    while True:
        data = stream.read(8192)
        if not data:
            break
        chunks.append(data)
        destination.write(data)
        destination.flush()


def run_argv(paths: RewindPaths, argv: list[str]) -> dict[str, object]:
    if not argv:
        raise RewindError("No command supplied. Use `rewind run -- COMMAND [ARGS...]`.")
    started = time.monotonic()
    try:
        process = subprocess.Popen(
            argv,
            cwd=paths.root,
            stdin=None,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            env={
                **os.environ,
                RECORDED_CHECK_ROOT_ENV: str(paths.root.resolve()),
            },
        )
    except OSError as exc:
        duration_ms = round((time.monotonic() - started) * 1000)
        exit_code = 127 if isinstance(exc, FileNotFoundError) else 126 if isinstance(exc, PermissionError) else 1
        message = f"Could not execute {argv[0]}: {exc}"
        # A launch failure is still durable validation evidence. Emitting and
        # recording it prevents a previous passing check from making the task
        # look green after the latest validation attempt did not run.
        sys.stderr.write(f"{message}\n")
        sys.stderr.flush()
        evidence = {
            "schema": "rewind.command-output.v1",
            "argv": argv,
            "exit_code": exit_code,
            "duration_ms": duration_ms,
            "stdout": "",
            "stderr": f"{message}\n",
            "launch_error": {
                "type": type(exc).__name__,
                "errno": exc.errno,
                "message": str(exc),
            },
        }
        evidence_hash = put_object(paths, canonical_bytes(evidence))
        return {
            "argv": argv,
            "exit_code": exit_code,
            "duration_ms": duration_ms,
            "passed": False,
            "kind": classify_command(argv),
            "evidence_sha256": evidence_hash,
            "launch_error": message,
        }
    assert process.stdout is not None and process.stderr is not None
    stdout_chunks: list[bytes] = []
    stderr_chunks: list[bytes] = []
    threads = [
        threading.Thread(
            target=_pump,
            args=(process.stdout, sys.stdout.buffer, stdout_chunks),
            daemon=True,
        ),
        threading.Thread(
            target=_pump,
            args=(process.stderr, sys.stderr.buffer, stderr_chunks),
            daemon=True,
        ),
    ]
    for thread in threads:
        thread.start()
    return_code = process.wait()
    for thread in threads:
        thread.join()
    duration_ms = round((time.monotonic() - started) * 1000)
    evidence = {
        "schema": "rewind.command-output.v1",
        "argv": argv,
        "exit_code": return_code,
        "duration_ms": duration_ms,
        "stdout": b"".join(stdout_chunks).decode("utf-8", errors="replace"),
        "stderr": b"".join(stderr_chunks).decode("utf-8", errors="replace"),
    }
    evidence_hash = put_object(paths, canonical_bytes(evidence))
    return {
        "argv": argv,
        "exit_code": return_code,
        "duration_ms": duration_ms,
        "passed": return_code == 0,
        "kind": classify_command(argv),
        "evidence_sha256": evidence_hash,
    }
