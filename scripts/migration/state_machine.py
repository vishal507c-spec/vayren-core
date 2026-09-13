"""Explicit migration lifecycle with machine-verifiable transitions.

No arbitrary status changes: each edge names the evidence predicate that
must hold. The predicates live in ``validator.py``/``parity.py`` so the
state machine itself stays a pure, easily tested transition table.
"""

from __future__ import annotations

from .models import STATES

# Ordered progression (REGRESSION may be entered from post-parity states).
_ORDER: dict[str, int] = {name: index for index, name in enumerate(STATES)}

_ALLOWED: dict[str, tuple[str, ...]] = {
    "DISCOVERED": ("ANALYZED",),
    "ANALYZED": ("PLANNED", "BLOCKED"),
    "PLANNED": ("BLOCKED", "READY"),
    "BLOCKED": ("READY", "PLANNED"),
    "READY": ("IMPLEMENTING", "BLOCKED"),
    "IMPLEMENTING": ("IMPLEMENTED", "BLOCKED"),
    "IMPLEMENTED": ("PARITY_TESTING", "BLOCKED"),
    "PARITY_TESTING": ("PARITY_VERIFIED", "IMPLEMENTED", "BLOCKED"),
    "PARITY_VERIFIED": ("SHADOW_VALIDATED", "INTEGRATED", "BLOCKED", "REGRESSION"),
    "SHADOW_VALIDATED": ("INTEGRATED", "BLOCKED", "REGRESSION"),
    "INTEGRATED": ("RUST_CANONICAL", "BLOCKED", "REGRESSION"),
    "RUST_CANONICAL": ("PYTHON_DEPRECATED", "REGRESSION"),
    "PYTHON_DEPRECATED": ("PYTHON_QUARANTINED", "REGRESSION"),
    "PYTHON_QUARANTINED": ("PYTHON_REMOVED", "REGRESSION"),
    "PYTHON_REMOVED": ("FINAL_VERIFIED", "REGRESSION"),
    "FINAL_VERIFIED": ("MIGRATED", "REGRESSION"),
    "MIGRATED": ("REGRESSION",),
    "REGRESSION": ("BLOCKED", "IMPLEMENTING"),
}

# Evidence required before entering the target state. The validator maps
# each requirement string to a real repository check; unknown strings fail.
REQUIREMENTS: dict[str, tuple[str, ...]] = {
    "ANALYZED": ("inventory_lists_unit",),
    "PLANNED": ("dependencies_known",),
    "READY": ("dependencies_migrated_or_glue", "rust_target_exists"),
    "IMPLEMENTING": ("rust_target_exists",),
    "IMPLEMENTED": ("rust_target_exists", "no_python_table"),
    "PARITY_TESTING": ("parity_suite_exists",),
    "PARITY_VERIFIED": ("parity_passed_fresh",),
    "SHADOW_VALIDATED": ("parity_passed_fresh", "shadow_passed_fresh"),
    "INTEGRATED": (
        "parity_passed_fresh",
        "production_uses_rust",
        "live_gate_where_required",
    ),
    "RUST_CANONICAL": (
        "parity_passed_fresh",
        "shadow_passed_fresh",
        "production_uses_rust",
        "live_gate_where_required",
        "explicit_promotion",
    ),
    "PYTHON_DEPRECATED": ("rust_is_canonical",),
    "PYTHON_QUARANTINED": ("python_not_imported_by_production",),
    "PYTHON_REMOVED": (
        "python_not_imported_by_production",
        "dependency_graph_clean",
    ),
    "FINAL_VERIFIED": ("removal_safe", "regression_suite_passed"),
    "MIGRATED": ("removal_safe", "regression_suite_passed", "final_evidence"),
}


def allowed_transitions(state: str) -> tuple[str, ...]:
    return _ALLOWED.get(state, ())


def is_allowed(from_state: str, to_state: str) -> bool:
    return to_state in _ALLOWED.get(from_state, ())


def requirements_for(state: str) -> tuple[str, ...]:
    return REQUIREMENTS.get(state, ())


def check_transition(from_state: str, to_state: str) -> tuple[bool, str]:
    """Return (ok, reason); unknown states always fail closed."""
    if from_state not in _ORDER:
        return False, f"unknown source state: {from_state}"
    if to_state not in _ORDER:
        return False, f"unknown target state: {to_state}"
    if not is_allowed(from_state, to_state):
        return False, f"transition {from_state} -> {to_state} is not permitted"
    return True, ""


__all__ = ["allowed_transitions", "is_allowed", "requirements_for", "check_transition"]
