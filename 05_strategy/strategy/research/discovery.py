"""Discovery — records potentially useful findings, never modifies strategy."""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

_CONFIDENCE_LEVELS = frozenset({"low", "medium", "high"})


@dataclass(frozen=True)
class Discovery:
    """Generic discovery — traceable to experiment/version/executions."""

    discovery_id: str
    strategy_id: str
    version_id: str
    experiment_id: str
    evidence: dict[str, Any]
    observed_effect: str
    confidence: str
    status: str
    created_at: str

    def __post_init__(self) -> None:
        """Reject empty identity fields and unknown confidence levels."""
        for field_name in ("discovery_id", "strategy_id", "version_id", "experiment_id"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"Discovery {field_name} must be a non-empty string")
        if not isinstance(self.confidence, str):
            raise ValueError(f"confidence must be one of {sorted(_CONFIDENCE_LEVELS)}")
        normalized = self.confidence.strip().lower()
        if normalized not in _CONFIDENCE_LEVELS:
            raise ValueError(
                f"confidence must be one of {sorted(_CONFIDENCE_LEVELS)}, got {self.confidence!r}"
            )
        object.__setattr__(self, "confidence", normalized)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, ensure_ascii=False, indent=2)

    @staticmethod
    def from_dict(data: dict[str, Any]) -> Discovery:
        return Discovery(
            discovery_id=str(data["discovery_id"]),
            strategy_id=str(data["strategy_id"]),
            version_id=str(data["version_id"]),
            experiment_id=str(data["experiment_id"]),
            evidence=dict(data.get("evidence", {})),
            observed_effect=str(data.get("observed_effect", "")),
            confidence=str(data.get("confidence", "low")),
            status=str(data.get("status", "candidate")),
            created_at=str(data.get("created_at", "")),
        )


def create_discovery(
    strategy_id: str,
    version_id: str,
    experiment_id: str,
    evidence: dict[str, Any],
    observed_effect: str,
    confidence: str = "low",
) -> Discovery:
    return Discovery(
        discovery_id=f"DISC-{uuid.uuid4().hex[:6].upper()}",
        strategy_id=strategy_id,
        version_id=str(version_id),
        experiment_id=str(experiment_id),
        evidence=dict(evidence),
        observed_effect=observed_effect,
        confidence=confidence,
        status="candidate",
        created_at=datetime.now(UTC).isoformat(),
    )
