"""Experiment — generic, reproducible."""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass(frozen=True)
class Hypothesis:
    """Explicit hypothesis."""

    text: str
    created_at: str = ""


@dataclass
class Experiment:
    """Generic experiment — input executions + hypothesis + config + result.

    Attributes:
        experiment_id: Stable UUID
        strategy_id: Strategy identifier
        version_id: Version identifier
        execution_ids: Input execution IDs
        hypothesis: Hypothesis text
        configuration: Generic config (params, filters, etc.)
        result: Analysis result (populated after run)
        created_at: ISO timestamp
    """

    experiment_id: str
    strategy_id: str
    version_id: str
    execution_ids: tuple[str, ...]
    hypothesis: Hypothesis
    configuration: dict[str, Any] = field(default_factory=dict)
    result: dict[str, Any] | None = None
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "experiment_id": self.experiment_id,
            "strategy_id": self.strategy_id,
            "version_id": self.version_id,
            "execution_ids": list(self.execution_ids),
            "hypothesis": asdict(self.hypothesis),
            "configuration": self.configuration,
            "result": self.result,
            "created_at": self.created_at,
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> Experiment:
        return Experiment(
            experiment_id=str(data["experiment_id"]),
            strategy_id=str(data["strategy_id"]),
            version_id=str(data["version_id"]),
            execution_ids=tuple(data.get("execution_ids", [])),
            hypothesis=Hypothesis(**data.get("hypothesis", {"text": ""})),
            configuration=dict(data.get("configuration", {})),
            result=data.get("result"),
            created_at=str(data.get("created_at", "")),
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, ensure_ascii=False, indent=2)


def create_experiment(
    strategy_id: str,
    version_id: str,
    execution_ids: list[str],
    hypothesis_text: str,
    configuration: dict[str, Any] | None = None,
) -> Experiment:
    return Experiment(
        experiment_id=f"EXP-{uuid.uuid4().hex[:6].upper()}",
        strategy_id=strategy_id,
        version_id=str(version_id),
        execution_ids=tuple(execution_ids),
        hypothesis=Hypothesis(text=hypothesis_text, created_at=datetime.now(UTC).isoformat()),
        configuration=dict(configuration or {}),
        result=None,
        created_at=datetime.now(UTC).isoformat(),
    )
