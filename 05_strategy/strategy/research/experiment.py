"""Experiment — generic, reproducible, persistent.

An experiment is the stable identity of one research run: strategy identity +
hypothesis + full configuration + data reference + fingerprints + status +
result summary. Persisted as one JSON file per experiment (see ``storage``);
a saved experiment survives restarts and reloads byte-identical results.

Backward compatible: records written by earlier versions (experiment_id,
strategy_id, version_id, execution_ids, hypothesis, configuration, result,
created_at) load with new fields defaulted.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

RESEARCH_ENGINE_VERSION = "1"

EXPERIMENT_STATUSES = (
    "DRAFT",
    "READY",
    "VALIDATING",
    "RUNNING",
    "ANALYZING",
    "VALIDATING_RESULT",
    "COMPLETED",
    "FAILED",
    "CANCELLED",
    "STALE",
    "INVALID",
    "NO_DATA",
)

TERMINAL_STATUSES = ("COMPLETED", "FAILED", "CANCELLED", "INVALID", "NO_DATA")


@dataclass(frozen=True)
class Hypothesis:
    """Explicit hypothesis plus optional research framing."""

    text: str
    created_at: str = ""
    research_question: str = ""
    expected_direction: str = ""
    expected_effect: str = ""
    conditions: tuple[str, ...] = ()
    assumptions: tuple[str, ...] = ()


@dataclass
class Experiment:
    """Generic experiment — input executions + hypothesis + config + result.

    Attributes:
        experiment_id: Stable UUID
        strategy_id: Strategy identifier
        version_id: Version identifier (legacy name; mirrors strategy_version)
        execution_ids: Input execution IDs
        hypothesis: Hypothesis text + framing
        configuration: Generic config (params, filters, etc.)
        result: Analysis result (populated after run)
        created_at: ISO timestamp
        strategy_version: Canonical strategy version (mirrors version_id)
        universe: Universe label (e.g. "NIFTY 500")
        symbols: Executed symbols in order
        timeframe: Executed timeframe label
        start_date/end_date: Executed calendar range (ISO YYYY-MM-DD)
        side: Executed side filter (LONG/SHORT/BOTH)
        initial_capital: Executed capital base
        slippage_pct/commission_pct: Executed cost assumptions
        parameters: Executed strategy parameters
        dataset_version: Dataset identity string for the run
        data_reference: Data snapshot (per-symbol bars/counts/range)
        engine_version: Research engine schema version
        engine_source_hash: Hash of the canonical execution core used
        strategy_source_hash: Hash of the executed strategy source
        config_fingerprint: Deterministic config identity
        result_fingerprint: Deterministic result identity
        status: One of EXPERIMENT_STATUSES
        validation_status: Latest validation verdict (NOT RUN/PASS/WARNING/FAIL/...)
        executed_at: ISO timestamp of execution ("" when never executed)
        result_summary: Executed outcome summary (JSON-safe)
        reproducibility: Full recreate metadata (JSON-safe)
        report: Structured research report (JSON-safe, optional)
    """

    experiment_id: str
    strategy_id: str
    version_id: str
    execution_ids: tuple[str, ...]
    hypothesis: Hypothesis
    configuration: dict[str, Any] = field(default_factory=dict)
    result: dict[str, Any] | None = None
    created_at: str = ""
    strategy_version: str = ""
    universe: str = ""
    symbols: tuple[str, ...] = ()
    timeframe: str = ""
    start_date: str = ""
    end_date: str = ""
    side: str = "BOTH"
    initial_capital: float = 0.0
    slippage_pct: float = 0.0
    commission_pct: float = 0.0
    parameters: dict[str, Any] = field(default_factory=dict)
    dataset_version: str = ""
    data_reference: dict[str, Any] = field(default_factory=dict)
    engine_version: str = RESEARCH_ENGINE_VERSION
    engine_source_hash: str = ""
    strategy_source_hash: str = ""
    config_fingerprint: str = ""
    result_fingerprint: str = ""
    status: str = "DRAFT"
    validation_status: str = "NOT RUN"
    executed_at: str = ""
    result_summary: dict[str, Any] | None = None
    reproducibility: dict[str, Any] = field(default_factory=dict)
    report: dict[str, Any] | None = None
    analysis: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if not self.strategy_version and self.version_id:
            self.strategy_version = self.version_id
        elif not self.version_id and self.strategy_version:
            self.version_id = self.strategy_version
        if self.status not in EXPERIMENT_STATUSES:
            self.status = "DRAFT"

    def to_dict(self) -> dict[str, Any]:
        data = {
            "experiment_id": self.experiment_id,
            "strategy_id": self.strategy_id,
            "version_id": self.version_id,
            "execution_ids": list(self.execution_ids),
            "hypothesis": asdict(self.hypothesis),
            "configuration": self.configuration,
            "result": self.result,
            "created_at": self.created_at,
            "strategy_version": self.strategy_version,
            "universe": self.universe,
            "symbols": list(self.symbols),
            "timeframe": self.timeframe,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "side": self.side,
            "initial_capital": self.initial_capital,
            "slippage_pct": self.slippage_pct,
            "commission_pct": self.commission_pct,
            "parameters": self.parameters,
            "dataset_version": self.dataset_version,
            "data_reference": self.data_reference,
            "engine_version": self.engine_version,
            "engine_source_hash": self.engine_source_hash,
            "strategy_source_hash": self.strategy_source_hash,
            "config_fingerprint": self.config_fingerprint,
            "result_fingerprint": self.result_fingerprint,
            "status": self.status,
            "validation_status": self.validation_status,
            "executed_at": self.executed_at,
            "result_summary": self.result_summary,
            "reproducibility": self.reproducibility,
            "report": self.report,
            "analysis": self.analysis,
        }
        return data

    @staticmethod
    def from_dict(data: dict[str, Any]) -> Experiment:
        raw_hypothesis = data.get("hypothesis", {"text": ""})
        if isinstance(raw_hypothesis, str):
            hypothesis = Hypothesis(text=raw_hypothesis)
        else:
            allowed = {
                "text",
                "created_at",
                "research_question",
                "expected_direction",
                "expected_effect",
                "conditions",
                "assumptions",
            }
            clean = {k: v for k, v in dict(raw_hypothesis).items() if k in allowed}
            clean.setdefault("text", "")
            conditions = clean.get("conditions", ())
            assumptions = clean.get("assumptions", ())
            clean["conditions"] = tuple(conditions or ())
            clean["assumptions"] = tuple(assumptions or ())
            hypothesis = Hypothesis(**clean)
        return Experiment(
            experiment_id=str(data["experiment_id"]),
            strategy_id=str(data["strategy_id"]),
            version_id=str(data.get("version_id", data.get("strategy_version", ""))),
            execution_ids=tuple(data.get("execution_ids", [])),
            hypothesis=hypothesis,
            configuration=dict(data.get("configuration", {})),
            result=data.get("result"),
            created_at=str(data.get("created_at", "")),
            strategy_version=str(data.get("strategy_version", data.get("version_id", ""))),
            universe=str(data.get("universe", "")),
            symbols=tuple(data.get("symbols", ())),
            timeframe=str(data.get("timeframe", "")),
            start_date=str(data.get("start_date", "")),
            end_date=str(data.get("end_date", "")),
            side=str(data.get("side", "BOTH")),
            initial_capital=float(data.get("initial_capital", 0.0) or 0.0),
            slippage_pct=float(data.get("slippage_pct", 0.0) or 0.0),
            commission_pct=float(data.get("commission_pct", 0.0) or 0.0),
            parameters=dict(data.get("parameters", {})),
            dataset_version=str(data.get("dataset_version", "")),
            data_reference=dict(data.get("data_reference", {})),
            engine_version=str(data.get("engine_version", RESEARCH_ENGINE_VERSION)),
            engine_source_hash=str(data.get("engine_source_hash", "")),
            strategy_source_hash=str(data.get("strategy_source_hash", "")),
            config_fingerprint=str(data.get("config_fingerprint", "")),
            result_fingerprint=str(data.get("result_fingerprint", "")),
            status=str(data.get("status", "DRAFT")),
            validation_status=str(data.get("validation_status", "NOT RUN")),
            executed_at=str(data.get("executed_at", "")),
            result_summary=data.get("result_summary"),
            reproducibility=dict(data.get("reproducibility", {})),
            report=data.get("report"),
            analysis=data.get("analysis"),
        )

    def to_json(self) -> str:
        import json as _json

        return _json.dumps(self.to_dict(), sort_keys=True, ensure_ascii=False, indent=2)


def create_experiment(
    strategy_id: str,
    version_id: str,
    execution_ids: list[str],
    hypothesis_text: str,
    configuration: dict[str, Any] | None = None,
    **extras: Any,
) -> Experiment:
    """Create a DRAFT experiment (legacy positional signature preserved)."""
    now = datetime.now(UTC).isoformat()
    hypothesis = Hypothesis(
        text=str(hypothesis_text),
        created_at=now,
        research_question=str(extras.get("research_question", "")),
        expected_direction=str(extras.get("expected_direction", "")),
        expected_effect=str(extras.get("expected_effect", "")),
        conditions=tuple(extras.get("conditions", ()) or ()),
        assumptions=tuple(extras.get("assumptions", ()) or ()),
    )
    return Experiment(
        experiment_id=f"EXP-{uuid.uuid4().hex[:6].upper()}",
        strategy_id=str(strategy_id),
        version_id=str(version_id),
        execution_ids=tuple(execution_ids),
        hypothesis=hypothesis,
        configuration=dict(configuration or {}),
        result=None,
        created_at=now,
        strategy_version=str(extras.get("strategy_version", version_id)),
        universe=str(extras.get("universe", "")),
        symbols=tuple(extras.get("symbols", ()) or ()),
        timeframe=str(extras.get("timeframe", "")),
        start_date=str(extras.get("start_date", "")),
        end_date=str(extras.get("end_date", "")),
        side=str(extras.get("side", "BOTH")),
        initial_capital=float(extras.get("initial_capital", 0.0) or 0.0),
        slippage_pct=float(extras.get("slippage_pct", 0.0) or 0.0),
        commission_pct=float(extras.get("commission_pct", 0.0) or 0.0),
        parameters=dict(extras.get("parameters", {})),
        dataset_version=str(extras.get("dataset_version", "")),
        data_reference=dict(extras.get("data_reference", {})),
        status="DRAFT",
    )


def is_stale(experiment: Experiment, current_config_fingerprint: str) -> bool:
    """True when meaningful inputs changed since execution (fingerprint mismatch)."""
    if not experiment.config_fingerprint or not experiment.executed_at:
        return False
    return experiment.config_fingerprint != current_config_fingerprint
