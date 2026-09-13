"""Agent data model: tasks, specs, reports and outcomes."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Expr:
    """One node of the kernel expression tree (JSON-serializable dict)."""

    node: dict

    def to_dict(self) -> dict:
        return self.node


@dataclass(frozen=True)
class KernelInput:
    name: str
    type: str  # "f64" | "int" | "bool"
    source: str  # e.g. "request.quantity" / "policy.max_notional_present"

    def to_dict(self) -> dict:
        return {"name": self.name, "type": self.type, "source": self.source}


@dataclass(frozen=True)
class KernelCheck:
    name: str
    expr: dict
    bit: int

    def to_dict(self) -> dict:
        return {"name": self.name, "expr": self.expr, "bit": self.bit}


@dataclass(frozen=True)
class KernelSpec:
    """Auditable IR between the Python source and both generated targets."""

    unit_id: str
    inputs: tuple = ()
    checks: tuple = ()
    orchestration: tuple = ()
    check_order: tuple = ()

    def to_dict(self) -> dict:
        return {
            "unit": self.unit_id,
            "inputs": [i.to_dict() for i in self.inputs],
            "checks": [c.to_dict() for c in self.checks],
            "orchestration": list(self.orchestration),
            "check_order": list(self.check_order),
        }


@dataclass(frozen=True)
class AnalysisReport:
    unit_id: str
    migratable: bool
    reason: str
    kernel_checks: tuple = ()
    orchestration_checks: tuple = ()
    refused_slint: bool = False
    warnings: tuple = ()


@dataclass(frozen=True)
class AttemptRecord:
    stage: str
    attempt: int
    ok: bool
    detail: str = ""


@dataclass(frozen=True)
class AgentOutcome:
    unit_id: str
    # PROMOTED (this run) | CANONICAL (already, re-verified) |
    # INTEGRATED | BLOCKED (honest, evidence recorded) | FAILED
    verdict: str
    stages: tuple = field(default_factory=tuple)
    evidence: tuple = field(default_factory=tuple)


__all__ = [
    "Expr",
    "KernelInput",
    "KernelCheck",
    "KernelSpec",
    "AnalysisReport",
    "AttemptRecord",
    "AgentOutcome",
]
