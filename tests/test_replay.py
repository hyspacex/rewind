from __future__ import annotations

from pathlib import Path

from rewind.demo import _base_lifecycle
from rewind.replay import replay_action


def test_tampered_evidence_fails_l1(tmp_path: Path) -> None:
    events, public, objects, action = _base_lifecycle(
        tmp_path / "tampered",
        policy_version="v2",
        approval=True,
    )
    evidence = next(path for path in objects.iterdir() if path.read_bytes() == b"14 tests passed\n")
    evidence.write_bytes(b"changed\n")
    result = replay_action(events, public, objects, action)
    assert result["stages"][0]["passed"]
    assert not result["stages"][1]["passed"]


def test_approval_absent_fails_l2(tmp_path: Path) -> None:
    events, public, objects, action = _base_lifecycle(
        tmp_path / "absent",
        policy_version="v1",
        approval=False,
    )
    result = replay_action(events, public, objects, action)
    assert [stage["passed"] for stage in result["stages"][:2]] == [True, True]
    assert not result["stages"][2]["passed"]
    assert result["stages"][3]["summary"] == "Binding must pass before authority is evaluated."


def test_role_revocation_fails_only_l3(tmp_path: Path) -> None:
    events, public, objects, action = _base_lifecycle(
        tmp_path / "revoked",
        policy_version="v2",
        approval=True,
        revoke_before_deploy=True,
    )
    result = replay_action(events, public, objects, action)
    assert [stage["passed"] for stage in result["stages"]] == [True, True, True, False]
    assert result["historical"]["allowed"] is False
    assert "deployment was not authorized" in result["conclusion"]


def test_policy_evolution_breaks_current_verdict(tmp_path: Path) -> None:
    events, public, objects, action = _base_lifecycle(
        tmp_path / "evolution",
        policy_version="v1",
        approval=True,
        proposer="alice",
        approver="alice",
        activate_v2_after=True,
    )
    result = replay_action(events, public, objects, action)
    assert result["historical"] == {"policy_id": "v1", "allowed": True, "reasons": []}
    assert result["current"]["policy_id"] == "v2"
    assert result["current"]["allowed"] is False
    assert result["drift"] == "tightening or breaking drift"
