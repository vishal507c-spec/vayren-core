"""Governance — Decision separate from Validation, user approval, audit trail.

Invariants:
- Validation ≠ Decision ≠ Deployment (a PASS does not auto-deploy or auto-accept).
- Decision never modifies historical versions, never deploys, never creates a version.
- Governance states include DRAFT/EXPLORATORY/UNDER_REVIEW/VALIDATED/REJECTED/ARCHIVED etc.
- Decision is immutable append-only (never overwritten).
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Allowed governance states — validation remains separate
DECISION_STATUSES = (
    "DRAFT",
    "EXPLORATORY",
    "UNDER_REVIEW",
    "VALIDATED",
    "REJECTED",
    "ARCHIVED",
    "ACCEPTED",
    "REJECTED_FOR_RESEARCH",
)


@dataclass(frozen=True)
class Decision:
    """Generic governance decision — does NOT deploy or modify strategy.

    A decision references exact version/discovery/validation/evidence ids
    but performs no side effect itself. Deployment or new version creation
    must be an explicit separate action via evolution/governance workflow.

    Attributes:
        decision_id: Stable id.
        strategy_id: Canonical StrategyRecord.id.
        version_id: Version under decision.
        discovery_id: Discovery that triggered decision.
        validation_id: Validation that informed decision.
        evidence_ids: Evidence records supporting decision.
        rationale: Human/audit rationale.
        status: One of DECISION_STATUSES (or custom but should map).
        created_at: ISO timestamp.
        metadata: Extra audit metadata.
        initiated_by: Actor label (user/system) — generic, not identity-specific.
    """

    decision_id: str
    strategy_id: str
    version_id: str
    discovery_id: str
    validation_id: str
    evidence_ids: tuple[str, ...]
    status: str  # see DECISION_STATUSES
    rationale: str
    created_at: str
    metadata: dict[str, Any] = field(default_factory=dict)
    initiated_by: str = "user"

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["evidence_ids"] = list(self.evidence_ids)
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, ensure_ascii=False, indent=2)

    @staticmethod
    def from_dict(data: dict[str, Any]) -> Decision:
        return Decision(
            decision_id=str(data["decision_id"]),
            strategy_id=str(data["strategy_id"]),
            version_id=str(data["version_id"]),
            discovery_id=str(data["discovery_id"]),
            validation_id=str(data["validation_id"]),
            evidence_ids=tuple(data.get("evidence_ids", [])),
            status=str(data["status"]),
            rationale=str(data.get("rationale", "")),
            created_at=str(data["created_at"]),
            metadata=dict(data.get("metadata", {})),
            initiated_by=str(data.get("initiated_by", "user")),
        )


def create_decision(
    strategy_id: str,
    version_id: str,
    discovery_id: str,
    validation_id: str,
    evidence_ids: list[str],
    status: str,
    rationale: str,
    initiated_by: str = "user",
    metadata: dict[str, Any] | None = None,
) -> Decision:
    """Create a decision — does NOT deploy, does NOT create a version, does NOT modify history.

    Caller remains responsible for separate deployment/evolution actions via
    explicit workflow (approve_proposal etc.). This function only records intent.
    """
    # Normalize status but allow custom if needed — preserve as given
    norm = str(status).upper().strip()
    # Keep as provided but uppercase canonical if matches known statuses
    if norm in DECISION_STATUSES:
        status = norm
    return Decision(
        decision_id=f"DEC-{uuid.uuid4().hex[:6].upper()}",
        strategy_id=str(strategy_id),
        version_id=str(version_id),
        discovery_id=str(discovery_id),
        validation_id=str(validation_id),
        evidence_ids=tuple(evidence_ids),
        status=str(status),
        rationale=str(rationale),
        created_at=datetime.now(UTC).isoformat(),
        metadata=dict(metadata or {}),
        initiated_by=initiated_by,
    )


def _governance_dir(data_dir: Path | str | None, sub: str) -> Path:
    if data_dir and Path(data_dir).is_dir():
        d = Path(data_dir) / "research" / sub
    else:
        d = Path.cwd() / ".vayren" / "research" / sub
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_decision(dec: Decision, data_dir: Path | str | None = None) -> Path:
    p = _governance_dir(data_dir, "decisions") / f"{dec.decision_id}.json"
    # Immutable append-only: never overwrite; distinct decision gets new file
    if p.exists():
        try:
            existing = Decision.from_dict(json.loads(p.read_text(encoding="utf-8")))
            if existing.to_dict() == dec.to_dict():
                return p
        except Exception:
            pass
        raise FileExistsError(f"decision id collision: {dec.decision_id} already exists")
    p.write_text(dec.to_json(), encoding="utf-8")
    # Lineage: VERSION -> DECISION, EVIDENCE -> DECISION, VALIDATION -> DECISION, DISCOVERY -> DECISION  # noqa: E501
    try:
        from .lineage import load_lineage, save_lineage

        g = load_lineage(data_dir)
        g.add_node("DECISION", dec.decision_id)
        g.add_node("STRATEGY", dec.strategy_id)
        g.add_node("VERSION", dec.version_id)
        g.add_edge("STRATEGY", dec.strategy_id, "DECISION", dec.decision_id, relationship="decided")
        g.add_edge(
            "VERSION", dec.version_id, "DECISION", dec.decision_id, relationship="decided_on"
        )
        if dec.discovery_id:
            g.add_node("DISCOVERY", dec.discovery_id)
            g.add_edge(
                "DISCOVERY",
                dec.discovery_id,
                "DECISION",
                dec.decision_id,
                relationship="decided_on",
            )
        if dec.validation_id:
            g.add_node("VALIDATION", dec.validation_id)
            g.add_edge(
                "VALIDATION",
                dec.validation_id,
                "DECISION",
                dec.decision_id,
                relationship="validated_by",
            )
        for eid in dec.evidence_ids:
            g.add_node("EVIDENCE", eid)
            g.add_edge("EVIDENCE", eid, "DECISION", dec.decision_id, relationship="supported_by")
        save_lineage(g, data_dir)
    except Exception:
        pass
    return p


def load_decision(decision_id: str, data_dir: Path | str | None = None) -> Decision | None:
    p = _governance_dir(data_dir, "decisions") / f"{decision_id}.json"
    if not p.exists():
        return None
    try:
        return Decision.from_dict(json.loads(p.read_text(encoding="utf-8")))
    except Exception:
        return None


def list_decisions(data_dir: Path | str | None = None) -> list[Decision]:
    d = _governance_dir(data_dir, "decisions")
    decs: list[Decision] = []
    for p in d.glob("*.json"):
        dec = load_decision(p.stem, data_dir)
        if dec:
            decs.append(dec)
    decs.sort(key=lambda x: x.created_at)
    return decs


# ── Audit Trail ──


@dataclass(frozen=True)
class AuditRecord:
    action: str
    target_type: str
    target_id: str
    actor: str
    timestamp: str
    details: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _audit_path(data_dir: Path | str | None) -> Path:
    if data_dir and Path(data_dir).is_dir():
        p = Path(data_dir) / "research" / "audit.jsonl"
    else:
        p = Path.cwd() / ".vayren" / "research" / "audit.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def append_audit(
    action: str,
    target_type: str,
    target_id: str,
    actor: str = "system",
    details: dict[str, Any] | None = None,
    data_dir: Path | str | None = None,
) -> None:
    rec = AuditRecord(
        action=str(action),
        target_type=str(target_type),
        target_id=str(target_id),
        actor=str(actor),
        timestamp=datetime.now(UTC).isoformat(),
        details=dict(details or {}),
    )
    p = _audit_path(data_dir)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec.to_dict(), sort_keys=True, ensure_ascii=False) + "\n")


def read_audit(data_dir: Path | str | None = None) -> list[AuditRecord]:
    p = _audit_path(data_dir)
    if not p.exists():
        return []
    recs: list[AuditRecord] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            d = json.loads(line)
            recs.append(AuditRecord(**d))
        except Exception:
            continue
    return recs
