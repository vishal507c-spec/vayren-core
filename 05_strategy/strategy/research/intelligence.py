"""Research Intelligence — generic automated discovery.

Systematically searches executions for candidate relationships, bounded and reproducible.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field  # noqa: F401
from typing import Any


@dataclass(frozen=True)
class CandidateHypothesis:
    """Candidate hypothesis — not yet validated."""

    hypothesis_id: str
    strategy_id: str
    version_id: str
    execution_ids: tuple[str, ...]
    feature: str
    condition: str
    baseline: dict[str, Any]
    observed: dict[str, Any]
    sample_size: int
    effect_size: float
    generation_method: str
    configuration: dict[str, Any]


@dataclass(frozen=True)
class IntelligenceRun:
    """One intelligence run — traceable, bounded."""

    run_id: str
    strategy_id: str
    version_id: str
    execution_ids: tuple[str, ...]
    configuration: dict[str, Any]
    hypotheses_tested: int
    candidates_found: int
    discoveries: tuple[str, ...]
    created_at: str
    result_summary: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, ensure_ascii=False, indent=2)
