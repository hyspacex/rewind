"""Network-free developer demo and deterministic forensic scenarios."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from nacl.signing import SigningKey

from .crypto import canonical_bytes, encode_key
from .events import EventLog
from .git_state import create_recovery_branch
from .receipt import build_receipt
from .replay import replay_action
from .report import render_report
from .store import RewindError, RewindPaths, initialize, packaged_policy, put_object
from .task import finish_task, run_check, start_task


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _git(root: Path, *args: str) -> None:
    process = subprocess.run(["git", *args], cwd=root, text=True, capture_output=True, check=False)
    if process.returncode:
        raise RewindError(process.stderr.strip())


def _audit_log(directory: Path) -> tuple[EventLog, Path, Path]:
    local = directory / ".rewind"
    objects = local / "objects" / "sha256"
    objects.mkdir(parents=True)
    signing_key = SigningKey(bytes(range(32)))
    public = local / "recorder.pub"
    public.write_text(encode_key(bytes(signing_key.verify_key)) + "\n", encoding="utf-8")
    log = EventLog(local / "events.jsonl", signing_key)
    log.append(
        "recorder_initialized",
        {"public_key": encode_key(bytes(signing_key.verify_key)), "threat_model": "trusted_local_recorder"},
    )
    return log, public, objects


def _object(objects: Path, data: bytes) -> str:
    digest = __import__("hashlib").sha256(data).hexdigest()
    (objects / digest).write_bytes(data)
    return digest


def _base_lifecycle(
    directory: Path,
    *,
    policy_version: str,
    approval: bool,
    proposer: str = "alice",
    approver: str = "bob",
    revoke_before_deploy: bool = False,
    activate_v2_after: bool = False,
) -> tuple[Path, Path, Path, str]:
    log, public, objects = _audit_log(directory)
    policy_hash = _object(objects, packaged_policy(policy_version))
    log.append(
        "policy_activated",
        {"policy_id": policy_version, "policy_object_sha256": policy_hash},
        actor="human",
    )
    action_id = "action_deploy_01"
    tree = "a" * 40
    artifact_hash = _object(objects, b"release artifact v1\n")
    evidence_hash = _object(objects, b"14 tests passed\n")
    log.append("role_granted", {"subject": "deploy-bot", "role": "deployer"}, actor="human")
    log.append(
        "command_evidence_recorded",
        {
            "action_id": action_id,
            "tree": tree,
            "passed": True,
            "evidence_sha256": evidence_hash,
            "argv": ["pytest", "-q"],
        },
        actor="agent",
    )
    log.append(
        "artifact_recorded",
        {
            "action_id": action_id,
            "tree": tree,
            "artifact_sha256": artifact_hash,
            "proposer": proposer,
            "protected_change": True,
        },
        actor=proposer,
    )
    if approval:
        log.append(
            "approval_recorded",
            {
                "action_id": action_id,
                "tree": tree,
                "artifact_sha256": artifact_hash,
                "approver": approver,
            },
            actor=approver,
        )
    if revoke_before_deploy:
        log.append("role_revoked", {"subject": "deploy-bot", "role": "deployer"}, actor="human")
    log.append(
        "deployment_observed",
        {
            "action_id": action_id,
            "tree": tree,
            "artifact_sha256": artifact_hash,
            "proposer": proposer,
            "actor": "deploy-bot",
        },
        actor="observer",
    )
    if activate_v2_after:
        v2_hash = _object(objects, packaged_policy("v2"))
        log.append(
            "policy_activated",
            {"policy_id": "v2", "policy_object_sha256": v2_hash},
            actor="human",
        )
    return log.path, public, objects, action_id


def _audit_html(results: list[dict[str, Any]], output: Path) -> None:
    cards = []
    for result in results:
        stages = "".join(
            f"<li><strong>{stage['level']} {stage['name']}</strong> "
            f"<span class=\"{'pass' if stage['passed'] else 'fail'}\">"
            f"{'PASS' if stage['passed'] else 'FAIL'}</span><br>{stage['summary']}</li>"
            for stage in result["stages"]
        )
        cards.append(
            f"<section><h2>{result['label']}</h2><p class=\"conclusion\">{result['conclusion']}</p>"
            f"<ol>{stages}</ol><div class=\"compare\"><div>Historical · {result['historical']['policy_id']}<br>"
            f"<strong>{'ALLOW' if result['historical']['allowed'] else 'DENY'}</strong></div>"
            f"<div>Current · {result['current']['policy_id']}<br>"
            f"<strong>{'ALLOW' if result['current']['allowed'] else 'DENY'}</strong></div>"
            f"<div>Drift<br><strong>{result['drift']}</strong></div></div></section>"
        )
    output.write_text(
        """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Rewind forensic replay</title><style>
body{margin:0;background:#0d0f11;color:#eef1f3;font:17px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
main{width:min(1120px,calc(100% - 48px));margin:auto;padding:44px 0}.brand{color:#9aa3ab;letter-spacing:.16em;font:700 13px monospace}
h1{font-size:48px;margin:12px 0 30px}section{background:#15181b;border:1px solid #30363c;margin:16px 0;padding:25px}
h2{margin-top:0}.conclusion{font-size:21px}ol{padding-left:24px}li{margin:13px 0}.pass{color:#62c58b}.fail{color:#ee6b65}
.compare{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-top:20px}.compare div{background:#0d0f11;border:1px solid #30363c;padding:16px;color:#9aa3ab}.compare strong{color:#eef1f3}
</style></head><body><main><div class="brand">REWIND / SIGNED AUDIT REPLAY</div><h1>Authenticity is not authority.</h1>"""
        + "".join(cards)
        + "</main></body></html>",
        encoding="utf-8",
    )


def create_demo(output: Path) -> dict[str, Any]:
    output = output.expanduser().resolve()
    if output.exists() and any(output.iterdir()):
        raise RewindError(f"Demo output directory is not empty: {output}")
    output.mkdir(parents=True, exist_ok=True)
    repo = output / "developer-task"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "Rewind Demo")
    _git(repo, "config", "user.email", "demo@rewind.local")
    _write(repo / ".gitignore", "__pycache__/\n*.py[cod]\n")
    _write(
        repo / "demo_app/upload/worker.py",
        "def upload(send, payload):\n    return send(payload)\n",
    )
    _write(repo / "demo_app/upload/__init__.py", "")
    _write(repo / "demo_app/__init__.py", "")
    _write(
        repo / "tests/test_worker.py",
        "import unittest\nfrom demo_app.upload.worker import upload\n\n"
        "class UploadTests(unittest.TestCase):\n"
        "    def test_upload(self):\n        self.assertEqual(upload(lambda value: value, 'ok'), 'ok')\n",
    )
    _write(repo / "pyproject.toml", "[project]\nname='demo-upload'\nversion='0.0.1'\ndependencies=[]\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "demo: initial upload worker")
    demo_paths = initialize(repo)
    started = start_task(
        demo_paths,
        "Add retry handling to the upload worker",
        ["demo_app/upload/**", "tests/**"],
    )
    _write(
        repo / "demo_app/upload/worker.py",
        "def upload(send, payload, attempts=3):\n"
        "    last_error = None\n"
        "    for _ in range(attempts):\n"
        "        try:\n            return send(payload)\n"
        "        except OSError as error:\n            last_error = error\n"
        "    raise last_error\n",
    )
    _write(
        repo / "tests/test_worker.py",
        "import unittest\nfrom demo_app.upload.worker import upload\n\n"
        "class UploadTests(unittest.TestCase):\n"
        "    def test_success(self):\n        self.assertEqual(upload(lambda value: value, 'ok'), 'ok')\n\n"
        "    def test_retry(self):\n"
        "        calls = []\n"
        "        def flaky(value):\n"
        "            calls.append(value)\n"
        "            if len(calls) < 3: raise OSError('temporary')\n"
        "            return value\n"
        "        self.assertEqual(upload(flaky, 'ok'), 'ok')\n"
        "        self.assertEqual(len(calls), 3)\n",
    )
    _write(
        repo / "pyproject.toml",
        "[project]\nname='demo-upload'\nversion='0.0.1'\ndependencies=['backoff>=2']\n",
    )
    run_check(demo_paths, [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-q"])
    _write(repo / "deploy.yaml", "service: upload-worker\nreplicas: 3\n")
    with (repo / "demo_app/upload/worker.py").open("a", encoding="utf-8") as handle:
        handle.write("\n# Metrics hook added after the recorded test.\n")
    finished = finish_task(demo_paths)
    receipt = build_receipt(demo_paths, started["task_id"])
    report_path = render_report(receipt, output / "task-report.html")
    (output / "task-receipt.json").write_bytes(canonical_bytes(receipt) + b"\n")
    recovery = receipt["recommended_recovery"]
    assert recovery
    create_recovery_branch(repo, "rewind/retry-last-tested", recovery["commit"])

    scenario_specs = [
        ("01-tampered-evidence", dict(policy_version="v2", approval=True), True),
        ("02-approval-absent", dict(policy_version="v1", approval=False), False),
        (
            "03-role-revoked",
            dict(policy_version="v2", approval=True, revoke_before_deploy=True),
            False,
        ),
        (
            "04-policy-evolution",
            dict(
                policy_version="v1",
                approval=True,
                proposer="alice",
                approver="alice",
                activate_v2_after=True,
            ),
            False,
        ),
    ]
    audit_results: list[dict[str, Any]] = []
    labels = {
        "01-tampered-evidence": "Tampered evidence blob",
        "02-approval-absent": "Approval absent from a valid lifecycle",
        "03-role-revoked": "Role revoked before observed deployment",
        "04-policy-evolution": "Policy evolution",
    }
    for name, options, tamper in scenario_specs:
        directory = output / "forensics" / name
        directory.mkdir(parents=True)
        events_path, public, objects, action_id = _base_lifecycle(directory, **options)
        if tamper:
            evidence_event = next(
                event
                for event in __import__("rewind.events", fromlist=["parse_jsonl"]).parse_jsonl(events_path)
                if event[1]["type"] == "command_evidence_recorded"
            )[1]
            (objects / evidence_event["payload"]["evidence_sha256"]).write_bytes(b"tampered output\n")
        result = replay_action(events_path, public, objects, action_id)
        result["label"] = labels[name]
        (directory / "result.json").write_bytes(canonical_bytes(result) + b"\n")
        audit_results.append(result)
    audit_report = output / "forensic-report.html"
    _audit_html(audit_results, audit_report)
    summary = {
        "output": str(output),
        "task_id": started["task_id"],
        "task_report": str(report_path),
        "recovery_branch": "rewind/retry-last-tested",
        "forensic_report": str(audit_report),
        "forensic_results": [
            {
                "label": result["label"],
                "historical_allowed": result["historical"]["allowed"],
                "current_allowed": result["current"]["allowed"],
                "stages": {stage["level"]: stage["passed"] for stage in result["stages"]},
            }
            for result in audit_results
        ],
    }
    (output / "README.txt").write_text(
        "REWIND JUDGE DEMO\n\n"
        "Open task-report.html for the developer receipt.\n"
        "Open forensic-report.html for signed historical replay.\n"
        "The disposable Git repository is in developer-task/.\n",
        encoding="utf-8",
    )
    return summary
