"""Evidence Store — generic, immutable, hash-based, traceable.

No strategy-specific branches (OBR/SMA/etc. never appear).
Full deterministic SHA-256 for identity; append-only.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _hash_evidence(
    strategy_id: str,
    version_id: str,
    source_type: str,
    source_id: str,
    metric: str,
    value: Any,
    context: dict[str, Any],
    validation_id: str | None = None,
    experiment_id: str | None = None,
    discovery_id: str | None = None,
) -> str:
    """Deterministic full SHA-256 over canonical evidence identity.

    Includes strategy/version/source/metric/value/context + optional links.
    No truncated hash — full 64-char hex.
    """
    canonical = json.dumps(
        {
            "strategy_id": strategy_id,
            "version_id": version_id,
            "source_type": source_type,
            "source_id": source_id,
            "metric": metric,
            "value": value,
            "context": context,
            "validation_id": validation_id or "",
            "experiment_id": experiment_id or "",
            "discovery_id": discovery_id or "",
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Evidence:
    """Generic evidence — immutable, traceable to version/execution/research.

    Attributes:
        evidence_id: Stable id (EVID-XXXX).
        strategy_id: Canonical StrategyRecord.id (UUID).
        version_id: Version that produced the source.
        source_type: execution | experiment | discovery | validation | dataset | etc.
        source_id: Id of the source object.
        metric: Generic metric name (e.g., expectancy, win_rate, sharpe).
        value: Measured value — never fabricated.
        context: Extra context (symbol, timeframe, counts...).
        validation_id: Link to ValidationResult if applicable.
        experiment_id: Link to Experiment if applicable.
        discovery_id: Link to Discovery if applicable.
        created_at: ISO timestamp (metadata, not part of identity).
        hash: Full deterministic SHA-256 of canonical identity.
    """

    evidence_id: str
    strategy_id: str
    version_id: str
    source_type: str
    source_id: str
    metric: str
    value: Any
    context: dict[str, Any]
    validation_id: str | None
    experiment_id: str | None
    discovery_id: str | None
    created_at: str
    hash: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, ensure_ascii=False, indent=2)

    @staticmethod
    def from_dict(data: dict[str, Any]) -> Evidence:
        # Backward compat: old files lack experiment_id/discovery_id
        return Evidence(
            evidence_id=str(data["evidence_id"]),
            strategy_id=str(data["strategy_id"]),
            version_id=str(data["version_id"]),
            source_type=str(data["source_type"]),
            source_id=str(data["source_id"]),
            metric=str(data["metric"]),
            value=data["value"],
            context=dict(data.get("context", {})),
            validation_id=data.get("validation_id"),
            experiment_id=data.get("experiment_id"),
            discovery_id=data.get("discovery_id"),
            created_at=str(data["created_at"]),
            hash=str(data["hash"]),
        )


def create_evidence(
    strategy_id: str,
    version_id: str,
    source_type: str,
    source_id: str,
    metric: str,
    value: Any,
    context: dict[str, Any] | None = None,
    validation_id: str | None = None,
    experiment_id: str | None = None,
    discovery_id: str | None = None,
) -> Evidence:
    ctx = dict(context or {})
    h = _hash_evidence(
        str(strategy_id),
        str(version_id),
        str(source_type),
        str(source_id),
        str(metric),
        value,
        ctx,
        validation_id,
        experiment_id,
        discovery_id,
    )
    evidence_id = f"EVID-{uuid.uuid4().hex[:6].upper()}"
    # For traceability: if source_type implies experiment/discovery, ensure ids aligned
    # but we keep generic — caller decides
    return Evidence(
        evidence_id=evidence_id,
        strategy_id=str(strategy_id),
        version_id=str(version_id),
        source_type=str(source_type),
        source_id=str(source_id),
        metric=str(metric),
        value=value,
        context=ctx,
        validation_id=validation_id,
        experiment_id=experiment_id,
        discovery_id=discovery_id,
        created_at=datetime.now(UTC).isoformat(),
        hash=h,
    )


def _evidence_dir(data_dir: Path | str | None) -> Path:
    if data_dir and Path(data_dir).is_dir():
        d = Path(data_dir) / "research" / "evidence"
    else:
        d = Path.cwd() / ".vayren" / "research" / "evidence"
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_evidence(ev: Evidence, data_dir: Path | str | None = None) -> Path:
    p = _evidence_dir(data_dir) / f"{ev.evidence_id}.json"
    # Immutable append-only: never overwrite; distinct evidence gets new file
    if p.exists():
        try:
            existing = Evidence.from_dict(json.loads(p.read_text(encoding="utf-8")))
            if existing.hash == ev.hash and existing.evidence_id == ev.evidence_id:
                return p
        except Exception:
            pass
        # If id collision with different content, do not overwrite — signal error
        # Keep existing file, caller should create new evidence_id
        raise FileExistsError(f"evidence id collision: {ev.evidence_id} already exists")
    p.write_text(ev.to_json(), encoding="utf-8")
    # Lineage: source -> EVIDENCE, VERSION -> EVIDENCE, STRATEGY -> EVIDENCE
    try:
        from .lineage import load_lineage, save_lineage

        g = load_lineage(data_dir)
        g.add_node("EVIDENCE", ev.evidence_id)
        g.add_node("STRATEGY", ev.strategy_id)
        g.add_node("VERSION", ev.version_id)
        g.add_edge(
            "STRATEGY", ev.strategy_id, "EVIDENCE", ev.evidence_id, relationship="has_evidence"
        )
        g.add_edge(
            "VERSION", ev.version_id, "EVIDENCE", ev.evidence_id, relationship="has_evidence"
        )
        # Generic source link
        if ev.source_type and ev.source_id:
            g.add_node(ev.source_type.upper(), ev.source_id)
            g.add_edge(
                ev.source_type.upper(),
                ev.source_id,
                "EVIDENCE",
                ev.evidence_id,
                relationship="supported_by",
            )
        if ev.experiment_id:
            g.add_node("EXPERIMENT", ev.experiment_id)
            g.add_edge(
                "EXPERIMENT", ev.experiment_id, "EVIDENCE", ev.evidence_id, relationship="produced"
            )
        if ev.discovery_id:
            g.add_node("DISCOVERY", ev.discovery_id)
            g.add_edge(
                "DISCOVERY",
                ev.discovery_id,
                "EVIDENCE",
                ev.evidence_id,
                relationship="validated_by",
            )
        if ev.validation_id:
            g.add_node("VALIDATION", ev.validation_id)
            g.add_edge(
                "VALIDATION",
                ev.validation_id,
                "EVIDENCE",
                ev.evidence_id,
                relationship="validated_by",
            )
        save_lineage(g, data_dir)
    except Exception:
        pass
    return p


def load_evidence(evidence_id: str, data_dir: Path | str | None = None) -> Evidence | None:
    p = _evidence_dir(data_dir) / f"{evidence_id}.json"
    if not p.exists():
        return None
    try:
        return Evidence.from_dict(json.loads(p.read_text(encoding="utf-8")))
    except Exception:
        return None


def list_evidence(data_dir: Path | str | None = None) -> list[Evidence]:
    d = _evidence_dir(data_dir)
    evs: list[Evidence] = []
    for p in d.glob("*.json"):
        ev = load_evidence(p.stem, data_dir)
        if ev:
            evs.append(ev)
    evs.sort(key=lambda x: x.created_at)
    return evs
