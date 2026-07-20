from __future__ import annotations

import sys
from pathlib import Path

from rewind.receipt import build_receipt, dependency_files
from rewind.report import render_report
from rewind.store import initialize
from rewind.task import finish_task, run_check, start_task

from conftest import git


def test_receipt_finds_scope_dependencies_and_stale_evidence(git_repo: Path) -> None:
    (git_repo / "demo_app/upload").mkdir(parents=True)
    (git_repo / "demo_app/upload/worker.py").write_text("VALUE = 1\n")
    (git_repo / "pyproject.toml").write_text("[project]\nname='fixture'\n")
    git(git_repo, "add", ".")
    git(git_repo, "commit", "-m", "add fixture")
    paths = initialize(git_repo)
    task = start_task(paths, "Add retries", ["demo_app/upload/**", "tests/**"])

    (git_repo / "demo_app/upload/worker.py").write_text("VALUE = 2\n")
    (git_repo / "tests").mkdir()
    (git_repo / "tests/test_sample.py").write_text("def test_ok():\n    assert True\n")
    (git_repo / "pyproject.toml").write_text("[project]\nname='fixture'\ndependencies=['backoff']\n")
    check = run_check(paths, [sys.executable, "-m", "pytest", "-q"])
    assert check["passed"]
    (git_repo / "deploy.yaml").write_text("replicas: 2\n")
    (git_repo / "demo_app/upload/worker.py").write_text("VALUE = 3\n")
    finish_task(paths)

    receipt = build_receipt(paths, task["task_id"])
    assert "deploy.yaml" in receipt["outside_scope"]
    assert "pyproject.toml" in receipt["dependency_files"]
    assert set(receipt["changed_after_passing_evidence"]) == {
        "demo_app/upload/worker.py",
        "deploy.yaml",
    }
    assert receipt["recommended_recovery"]["checkpoint_id"] == check["checkpoint_id"]
    assert receipt["outcome"] == "Completed with items to review"

    report = render_report(receipt, git_repo / "report.html")
    html = report.read_text()
    assert "<!doctype html>" in html
    assert "Outside declared scope" in html
    assert "https://" not in html
    assert "<script" not in html


def test_dependency_patterns() -> None:
    assert dependency_files(
        [
            "frontend/package-lock.json",
            "requirements-dev.txt",
            "src/main.py",
            "Cargo.toml",
            "go.sum",
        ]
    ) == [
        "frontend/package-lock.json",
        "requirements-dev.txt",
        "Cargo.toml",
        "go.sum",
    ]

