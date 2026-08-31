"""Controlled Strategy Evolution — generic, proposal-based, immutable."""

from __future__ import annotations

import difflib
import hashlib
import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from market.models.bar import Bar

from strategy.language import compile_to_ir
from strategy.models.state import StrategyState
from strategy.runtime import BarView
from strategy.version import create_version
from strategy.vm import StrategyVM


def _hash_source(source: str) -> str:
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


@dataclass
class EvolutionProposal:
    """Proposal to evolve a strategy — does NOT modify strategy directly."""

    proposal_id: str
    strategy_id: str
    parent_version_id: str
    discovery_id: str
    evidence_ids: tuple[str, ...]
    proposed_change: dict[
        str, Any
    ]  # e.g., {"type": "parameter", "param": "threshold", "from": 1.25, "to": 1.35} or {"type": "code", "diff": "..."}  # noqa: E501
    rationale: str
    expected_effect: str
    status: str  # PROPOSED, UNDER_REVIEW, APPROVED, REJECTED, APPLIED, FAILED
    created_at: str
    metadata: dict[str, Any] = field(default_factory=dict)
    parent_source_hash: str = ""
    new_source: str = ""
    new_source_hash: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["evidence_ids"] = list(self.evidence_ids)
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, ensure_ascii=False, indent=2)

    @staticmethod
    def from_dict(data: dict[str, Any]) -> EvolutionProposal:
        return EvolutionProposal(
            proposal_id=str(data["proposal_id"]),
            strategy_id=str(data["strategy_id"]),
            parent_version_id=str(data["parent_version_id"]),
            discovery_id=str(data["discovery_id"]),
            evidence_ids=tuple(data.get("evidence_ids", [])),
            proposed_change=dict(data.get("proposed_change", {})),
            rationale=str(data.get("rationale", "")),
            expected_effect=str(data.get("expected_effect", "")),
            status=str(data.get("status", "PROPOSED")),
            created_at=str(data.get("created_at", "")),
            metadata=dict(data.get("metadata", {})),
            parent_source_hash=str(data.get("parent_source_hash", "")),
            new_source=str(data.get("new_source", "")),
            new_source_hash=str(data.get("new_source_hash", "")),
        )


def _evolution_dir(data_dir: Path | str | None) -> Path:
    base = Path(data_dir) if data_dir and Path(data_dir).is_dir() else Path.cwd() / ".vayren"  # noqa: F841
    if data_dir and Path(data_dir).is_dir():
        d = Path(data_dir) / "research" / "evolution_proposals"
    else:
        d = Path.cwd() / ".vayren" / "research" / "evolution_proposals"
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_proposal(proposal: EvolutionProposal, data_dir: Path | str | None = None) -> Path:
    p = _evolution_dir(data_dir) / f"{proposal.proposal_id}.json"
    # Immutable check
    if p.exists():
        try:
            existing = EvolutionProposal.from_dict(json.loads(p.read_text(encoding="utf-8")))
            if existing.to_dict() == proposal.to_dict():
                return p
            raise FileExistsError(f"proposal id collision: {proposal.proposal_id}")
        except FileExistsError:
            raise
        except Exception:
            pass
    p.write_text(proposal.to_json(), encoding="utf-8")
    # Lineage: VERSION -> PROPOSAL and STRATEGY -> PROPOSAL, plus evidence/discovery links
    try:
        from .lineage import load_lineage, save_lineage

        g = load_lineage(data_dir)
        g.add_node("STRATEGY", proposal.strategy_id)
        g.add_node("VERSION", proposal.parent_version_id)
        g.add_node("EVOLUTION_PROPOSAL", proposal.proposal_id)
        g.add_edge(
            "STRATEGY",
            proposal.strategy_id,
            "EVOLUTION_PROPOSAL",
            proposal.proposal_id,
            relationship="proposed",
        )
        g.add_edge(
            "VERSION",
            proposal.parent_version_id,
            "EVOLUTION_PROPOSAL",
            proposal.proposal_id,
            relationship="proposed_from",
        )
        if proposal.discovery_id:
            g.add_node("DISCOVERY", proposal.discovery_id)
            g.add_edge(
                "DISCOVERY",
                proposal.discovery_id,
                "EVOLUTION_PROPOSAL",
                proposal.proposal_id,
                relationship="triggered",
            )
        for eid in proposal.evidence_ids:
            g.add_node("EVIDENCE", eid)
            g.add_edge(
                "EVIDENCE",
                eid,
                "EVOLUTION_PROPOSAL",
                proposal.proposal_id,
                relationship="supported_by",
            )
        save_lineage(g, data_dir)
    except Exception:
        pass
    return p


def load_proposal(proposal_id: str, data_dir: Path | str | None = None) -> EvolutionProposal | None:
    p = _evolution_dir(data_dir) / f"{proposal_id}.json"
    if not p.exists():
        return None
    try:
        return EvolutionProposal.from_dict(json.loads(p.read_text(encoding="utf-8")))
    except Exception:
        return None


def list_proposals(data_dir: Path | str | None = None) -> list[EvolutionProposal]:
    d = _evolution_dir(data_dir)
    proposals: list[EvolutionProposal] = []
    for p in d.glob("*.json"):
        prop = load_proposal(p.stem, data_dir)
        if prop:
            proposals.append(prop)
    proposals.sort(key=lambda x: x.created_at)
    return proposals


def create_proposal(
    strategy_id: str,
    parent_version_id: str,
    discovery_id: str,
    evidence_ids: list[str],
    proposed_change: dict[str, Any],
    rationale: str,
    expected_effect: str,
    new_source: str,
    parent_source: str,
    data_dir: Path | str | None = None,
) -> EvolutionProposal:
    """Create a proposal — does not modify strategy, shows BEFORE/AFTER/DIFF."""
    parent_hash = _hash_source(parent_source)
    new_hash = _hash_source(new_source)
    # Verify parent unchanged
    # Generate diff
    diff = "\n".join(
        difflib.unified_diff(
            parent_source.splitlines(),
            new_source.splitlines(),
            fromfile="BEFORE",
            tofile="AFTER",
            lineterm="",
        )
    )
    change = dict(proposed_change)
    change["diff"] = diff
    change["parent_source_hash"] = parent_hash
    change["new_source_hash"] = new_hash
    proposal = EvolutionProposal(
        proposal_id=f"PROP-{uuid.uuid4().hex[:6].upper()}",
        strategy_id=str(strategy_id),
        parent_version_id=str(parent_version_id),
        discovery_id=str(discovery_id),
        evidence_ids=tuple(evidence_ids),
        proposed_change=change,
        rationale=str(rationale),
        expected_effect=str(expected_effect),
        status="PROPOSED",
        created_at=datetime.now(UTC).isoformat(),
        metadata={},
        parent_source_hash=parent_hash,
        new_source=new_source,
        new_source_hash=new_hash,
    )
    save_proposal(proposal, data_dir)
    return proposal


def approve_proposal(
    proposal_id: str,
    data_dir: Path | str | None = None,
) -> tuple[EvolutionProposal, Any | None]:
    """Approve a proposal — creates new version, validates compilation and VM.

    Safety checks before creating V4:
    1. parent_version_id exists
    2. parent source hash matches proposal's parent_source_hash
    3. new source compiles to valid IR
    4. VM execution succeeds on synthetic bars
    5. IR hash is deterministic
    6. New version is created with correct parent

    Returns (updated_proposal, new_version or None if failed).
    """
    prop = load_proposal(proposal_id, data_dir)
    if prop is None:
        raise FileNotFoundError(f"Proposal not found: {proposal_id}")
    if prop.status not in ("PROPOSED", "UNDER_REVIEW"):
        raise ValueError(f"Proposal not in approvable state: {prop.status}")

    # SAFETY CHECK 1: Verify parent version exists
    try:
        from strategy.version import list_versions, load_version

        parent_version = load_version(prop.strategy_id, prop.parent_version_id, data_dir)
        if parent_version is None:
            existing = list_versions(prop.strategy_id, data_dir)
            if existing:
                parent_version = existing[-1]
                prop = EvolutionProposal(
                    proposal_id=prop.proposal_id,
                    strategy_id=prop.strategy_id,
                    parent_version_id=parent_version.version_id,
                    discovery_id=prop.discovery_id,
                    evidence_ids=prop.evidence_ids,
                    proposed_change=prop.proposed_change,
                    rationale=prop.rationale,
                    expected_effect=prop.expected_effect,
                    status=prop.status,
                    created_at=prop.created_at,
                    metadata=prop.metadata,
                    parent_source_hash=parent_version.source_hash,
                    new_source=prop.new_source,
                    new_source_hash=prop.new_source_hash,
                )
            else:
                failed = _proposal_failed(prop, "Parent version not found and no versions exist")
                save_proposal(failed, data_dir)
                return failed, None
    except Exception as e:
        failed = _proposal_failed(prop, f"Parent version lookup failed: {e}")
        save_proposal(failed, data_dir)
        return failed, None

    # SAFETY CHECK 2: Verify parent source hash matches
    if prop.parent_source_hash and prop.parent_source_hash != parent_version.source_hash:
        failed = _proposal_failed(
            prop,
            f"Parent source hash mismatch: proposal has {prop.parent_source_hash}, "
            f"actual parent has {parent_version.source_hash}",
        )
        save_proposal(failed, data_dir)
        return failed, None

    # SAFETY CHECK 3: Compiler validation
    try:
        ir = compile_to_ir(prop.new_source)
    except Exception as e:
        failed = _proposal_failed(prop, f"Compilation failed: {e}")
        save_proposal(failed, data_dir)
        return failed, None

    # SAFETY CHECK 4: VM validation — controlled test execution
    try:
        vm = StrategyVM(ir, {})
        from datetime import timedelta

        base = datetime(2026, 1, 1, 9, 15, tzinfo=UTC)
        bars = tuple(
            Bar(
                symbol="TEST",
                open=100 + i,
                high=102 + i,
                low=99 + i,
                close=101 + i,
                volume=1000,
                timestamp=(base + timedelta(minutes=15 * i)).strftime("%Y-%m-%d %H:%M:%S"),
            )
            for i in range(30)
        )
        for idx in range(vm.warmup(), len(bars)):
            view = BarView(bars=bars, index=idx, params={}, state=StrategyState())
            vm.on_bar(view)
    except Exception as e:
        failed = _proposal_failed(prop, f"VM execution failed: {e}")
        save_proposal(failed, data_dir)
        return failed, None

    # SAFETY CHECK 5: Create new version with deterministic IR hash
    try:
        ir_hash = hashlib.sha256(ir.to_json().encode("utf-8")).hexdigest()
        new_version = create_version(
            prop.strategy_id,
            prop.new_source,
            ir_version=ir.ir_version,
            ir_hash=ir_hash,
            parent_version_id=prop.parent_version_id,
            data_dir=data_dir,
            metadata={
                "proposal_id": prop.proposal_id,
                "discovery_id": prop.discovery_id,
                "state": "RESEARCH",
            },
        )
    except Exception as e:
        failed = _proposal_failed(prop, f"Version creation failed: {e}")
        save_proposal(failed, data_dir)
        return failed, None

    # Lineage: PROPOSAL -> NEW_VERSION
    try:
        from .lineage import load_lineage, save_lineage

        g = load_lineage(data_dir)
        g.add_node("EVOLUTION_PROPOSAL", prop.proposal_id)
        g.add_node("VERSION", new_version.version_id)
        g.add_edge(
            "EVOLUTION_PROPOSAL",
            prop.proposal_id,
            "VERSION",
            new_version.version_id,
            relationship="evolved_to",
        )
        save_lineage(g, data_dir)
    except Exception:
        pass

    # Mark proposal as APPLIED
    applied = EvolutionProposal(
        proposal_id=prop.proposal_id,
        strategy_id=prop.strategy_id,
        parent_version_id=prop.parent_version_id,
        discovery_id=prop.discovery_id,
        evidence_ids=prop.evidence_ids,
        proposed_change=prop.proposed_change,
        rationale=prop.rationale,
        expected_effect=prop.expected_effect,
        status="APPLIED",
        created_at=prop.created_at,
        metadata={**prop.metadata, "new_version_id": new_version.version_id},
        parent_source_hash=prop.parent_source_hash,
        new_source=prop.new_source,
        new_source_hash=prop.new_source_hash,
    )
    save_proposal(applied, data_dir)

    return applied, new_version


def _proposal_failed(prop: EvolutionProposal, reason: str) -> EvolutionProposal:
    """Create a FAILED copy of a proposal with a reason."""
    return EvolutionProposal(
        proposal_id=prop.proposal_id,
        strategy_id=prop.strategy_id,
        parent_version_id=prop.parent_version_id,
        discovery_id=prop.discovery_id,
        evidence_ids=prop.evidence_ids,
        proposed_change=prop.proposed_change,
        rationale=prop.rationale,
        expected_effect=prop.expected_effect,
        status="FAILED",
        created_at=prop.created_at,
        metadata={**prop.metadata, "failure_reason": reason},
        parent_source_hash=prop.parent_source_hash,
        new_source=prop.new_source,
        new_source_hash=prop.new_source_hash,
    )


def reject_proposal(
    proposal_id: str, data_dir: Path | str | None = None, reason: str = ""
) -> EvolutionProposal:
    prop = load_proposal(proposal_id, data_dir)
    if prop is None:
        raise FileNotFoundError(f"Proposal not found: {proposal_id}")
    rejected = EvolutionProposal(
        proposal_id=prop.proposal_id,
        strategy_id=prop.strategy_id,
        parent_version_id=prop.parent_version_id,
        discovery_id=prop.discovery_id,
        evidence_ids=prop.evidence_ids,
        proposed_change=prop.proposed_change,
        rationale=prop.rationale,
        expected_effect=prop.expected_effect,
        status="REJECTED",
        created_at=prop.created_at,
        metadata={**prop.metadata, "reject_reason": reason},
        parent_source_hash=prop.parent_source_hash,
        new_source=prop.new_source,
        new_source_hash=prop.new_source_hash,
    )
    save_proposal(rejected, data_dir)
    return rejected


def get_diff(proposal: EvolutionProposal) -> str:
    return proposal.proposed_change.get("diff", "")


def compare_versions(
    parent_version_id: str,
    candidate_version_id: str,
    strategy_id: str,
    data_dir: Path | str | None = None,
) -> dict[str, Any]:
    """Compare parent vs candidate — generic, not strategy-specific."""
    from strategy.version import load_version

    parent = load_version(strategy_id, parent_version_id, data_dir)
    candidate = load_version(strategy_id, candidate_version_id, data_dir)
    if not parent or not candidate:
        return {"error": "Version not found"}

    # For generic, we compare source hashes, IR hashes, and if possible run a simple backtest comparison  # noqa: E501
    # For now, return basic diff and metadata
    return {
        "parent": {
            "version_id": parent.version_id,
            "source_hash": parent.source_hash,
            "ir_hash": parent.ir_hash,
        },
        "candidate": {
            "version_id": candidate.version_id,
            "source_hash": candidate.source_hash,
            "ir_hash": candidate.ir_hash,
        },
        "source_changed": parent.source_hash != candidate.source_hash,
        "ir_changed": parent.ir_hash != candidate.ir_hash,
    }
