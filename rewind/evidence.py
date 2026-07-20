"""Recorded command execution bound to a Git checkpoint."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import BinaryIO

from .crypto import canonical_bytes
from .store import RewindError, RewindPaths, put_object

TEST_COMMANDS = {"pytest", "unittest", "tox", "nox", "cargo", "go", "npm", "pnpm", "yarn"}


def classify_command(argv: list[str]) -> str:
    command = Path(argv[0]).name.lower()
    if command.startswith("python") and len(argv) >= 3 and argv[1] == "-m":
        return "test" if argv[2] in {"pytest", "unittest"} else "check"
    if command in TEST_COMMANDS:
        if command in {"cargo", "go", "npm", "pnpm", "yarn"} and "test" not in argv[1:]:
            return "check"
        return "test"
    return "check"


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
            env=os.environ.copy(),
        )
    except OSError as exc:
        raise RewindError(f"Could not execute {argv[0]}: {exc}") from exc
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
