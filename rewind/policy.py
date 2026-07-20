"""The deliberately narrow Rewind policy model."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Policy:
    policy_id: str
    require_human_approval: bool
    require_successful_evidence: bool
    separation_of_duties: bool
    require_deployer_role: bool


def parse_policy(data: bytes) -> Policy:
    value: dict[str, Any] = json.loads(data)
    protected = value["protected_changes"]
    deployment = value["deployment"]
    return Policy(
        policy_id=str(value["id"]),
        require_human_approval=bool(protected["require_human_approval"]),
        require_successful_evidence=bool(protected["require_successful_evidence"]),
        separation_of_duties=bool(protected["separation_of_duties"]),
        require_deployer_role=bool(deployment["require_deployer_role"]),
    )

