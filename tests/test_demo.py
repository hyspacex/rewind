from __future__ import annotations

import subprocess
from pathlib import Path

from rewind.demo import create_demo
from rewind.replay import replay_action
from rewind.store import RewindPaths


def test_demo_outputs_reports_recovery_and_cli_replay_layout(tmp_path: Path) -> None:
    output = tmp_path / "judge"
    result = create_demo(output)
    assert Path(result["task_report"]).is_file()
    assert Path(result["forensic_report"]).is_file()

    developer_repo = output / "developer-task"
    assert (
        subprocess.run(
            ["git", "show-ref", "--verify", "--quiet", "refs/heads/rewind/retry-last-tested"],
            cwd=developer_repo,
            check=False,
        )
        .returncode
        == 0
    )

    fixture = RewindPaths(output / "forensics" / "03-role-revoked")
    assert fixture.public_key.is_file()
    replay = replay_action(
        fixture.events,
        fixture.public_key,
        fixture.objects,
        "action_deploy_01",
    )
    assert [stage["passed"] for stage in replay["stages"]] == [True, True, True, False]
