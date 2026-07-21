from __future__ import annotations

import sys
from pathlib import Path

from rewind.audit import (
    activate_policy,
    approve_action,
    change_role,
    observe_deployment,
    record_artifact,
)
from rewind.replay import replay_action
from rewind.store import initialize
from rewind.task import finish_task, run_check, start_task


def _record_public_lifecycle(
    root: Path,
    *,
    revoke_before_deployment: bool,
) -> dict[str, object]:
    paths = initialize(root)
    activate_policy(paths, "v2")
    start_task(paths, "Build a protected release", ["tracked.txt"])
    check = run_check(
        paths,
        [sys.executable, "-c", "print('release checks passed')"],
        action_id="action_release_01",
    )
    assert check["passed"]
    finish_task(paths)

    artifact = root / "release.tar"
    artifact.write_bytes(b"signed release artifact\n")
    record_artifact(paths, "action_release_01", artifact, "alice")
    approve_action(paths, "action_release_01", "bob")
    change_role(paths, "deploy-bot", "deployer", grant=True)
    if revoke_before_deployment:
        change_role(paths, "deploy-bot", "deployer", grant=False)
    observe_deployment(paths, "action_release_01", "deploy-bot")
    return replay_action(
        paths.events,
        paths.public_key,
        paths.objects,
        "action_release_01",
    )


def test_public_audit_lifecycle_can_replay_an_authorized_deployment(git_repo: Path) -> None:
    result = _record_public_lifecycle(git_repo, revoke_before_deployment=False)
    assert [stage["passed"] for stage in result["stages"]] == [True, True, True, True]
    assert result["historical"]["policy_id"] == "v2"
    assert result["historical"]["allowed"] is True
    assert result["current"]["allowed"] is True


def test_public_audit_lifecycle_detects_role_revocation(git_repo: Path) -> None:
    result = _record_public_lifecycle(git_repo, revoke_before_deployment=True)
    assert [stage["passed"] for stage in result["stages"]] == [True, True, True, False]
    assert result["historical"]["allowed"] is False
    assert "deployment was not authorized" in result["conclusion"]

