"""Core data model: verdicts, migration states and the migration manifest.

Evidence-driven, never declaration-driven: a manifest is only as fresh as
the hashes it pins. Any material change to either implementation invalidates
previous verification evidence.
"""

from __future__ import annotations

from dataclasses import dataclass, field


class Verdict:
    """Terminal comparison outcomes. Uncertainty is never a PASS."""

    PASS = "PASS"
    FAIL = "FAIL"
    BLOCKED = "BLOCKED"
    STALE = "STALE"
    INCONCLUSIVE = "INCONCLUSIVE"

    ALL = (PASS, FAIL, BLOCKED, STALE, INCONCLUSIVE)


# Full lifecycle in dependency-safe order. A unit advances one edge at a
# time and every edge has a machine-verifiable precondition.
STATES: tuple[str, ...] = (
    "DISCOVERED",
    "ANALYZED",
    "PLANNED",
    "BLOCKED",
    "READY",
    "IMPLEMENTING",
    "IMPLEMENTED",
    "PARITY_TESTING",
    "PARITY_VERIFIED",
    "SHADOW_VALIDATED",
    "INTEGRATED",
    "RUST_CANONICAL",
    "PYTHON_DEPRECATED",
    "PYTHON_QUARANTINED",
    "PYTHON_REMOVED",
    "FINAL_VERIFIED",
    "MIGRATED",
    # Terminal failure states (never count as progress).
    "REGRESSION",
)

CANONICAL_AFTER = "RUST_CANONICAL"


@dataclass(frozen=True)
class ParitySummary:
    status: str = Verdict.INCONCLUSIVE
    cases: int = 0
    passed: int = 0
    failed: int = 0
    inconclusive: int = 0


@dataclass(frozen=True)
class ShadowSummary:
    status: str = Verdict.INCONCLUSIVE
    comparisons: int = 0
    mismatches: int = 0


@dataclass(frozen=True)
class IntegrationSummary:
    status: str = Verdict.INCONCLUSIVE
    production_uses_rust: bool = False
    python_still_authoritative: bool = True
    detail: str = ""


@dataclass(frozen=True)
class RemovalSummary:
    status: str = Verdict.INCONCLUSIVE
    detail: str = ""


@dataclass(frozen=True)
class MigrationManifest:
    migration_id: str
    unit: str
    source: str
    target: str
    source_hash: str = ""
    target_hash: str = ""
    dependencies: tuple[str, ...] = ()
    state: str = "DISCOVERED"
    parity: ParitySummary = field(default_factory=ParitySummary)
    shadow: ShadowSummary = field(default_factory=ShadowSummary)
    integration: IntegrationSummary = field(default_factory=IntegrationSummary)
    authority: str = "python"
    removal: RemovalSummary = field(default_factory=RemovalSummary)
    evidence: tuple[str, ...] = ()
    live_critical: bool = False
    version: int = 1

    def to_dict(self) -> dict:
        return {
            "migration_id": self.migration_id,
            "unit": self.unit,
            "source": self.source,
            "target": self.target,
            "source_hash": self.source_hash,
            "target_hash": self.target_hash,
            "dependencies": list(self.dependencies),
            "state": self.state,
            "parity": {
                "status": self.parity.status,
                "cases": self.parity.cases,
                "passed": self.parity.passed,
                "failed": self.parity.failed,
            },
            "shadow": {
                "status": self.shadow.status,
                "comparisons": self.shadow.comparisons,
                "mismatches": self.shadow.mismatches,
            },
            "integration": {
                "status": self.integration.status,
                "production_uses_rust": self.integration.production_uses_rust,
                "python_still_authoritative": self.integration.python_still_authoritative,
            },
            "authority": self.authority,
            "removal": {"status": self.removal.status},
            "evidence": list(self.evidence),
            "live_critical": self.live_critical,
            "version": self.version,
        }


@dataclass(frozen=True)
class UnitDefinition:
    """Static registry entry for one behavioral migration unit."""

    unit_id: str
    description: str
    python_source: str
    rust_target: str
    dependencies: tuple[str, ...] = ()
    public_api: tuple[str, ...] = ()
    side_effects: str = "none"
    numerical: bool = False
    execution_path: str = ""
    live_critical: bool = False
    python_glue: bool = False


__all__ = [
    "Verdict",
    "STATES",
    "CANONICAL_AFTER",
    "ParitySummary",
    "ShadowSummary",
    "IntegrationSummary",
    "RemovalSummary",
    "MigrationManifest",
    "UnitDefinition",
]
