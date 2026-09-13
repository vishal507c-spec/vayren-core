"""Verify engine: run every check and advance states only on evidence.

``verify()`` is the single continuous-detection entry point used by the
gate: scan, re-pin hashes, execute parity and shadow harnesses, prove the
production path, then walk each unit forward along permitted state-machine
edges while the evidence requirements hold. Anything unverified stays
BLOCKED/STALE/INCONCLUSIVE — never PASS.
"""

from __future__ import annotations

import json
import time

from .config import GRAPH_PATH, INVENTORY_PATH, MIN_SHADOW_COMPARISONS
from .golden import persist_golden
from .graph import blockers_for, build_graph, graph_dict
from .manifest_store import append_evidence, load_all, refresh_hashes, save_manifest
from .models import IntegrationSummary, MigrationManifest, ParitySummary, ShadowSummary
from .parity import run_all_parity
from .scanner import inventory_dict, scan
from .shadow import run_all_shadow
from .state_machine import is_allowed
from .validator import current_hashes, production_uses_rust


def _stamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _advance(manifest: MigrationManifest, to_state: str, reason: str) -> MigrationManifest:
    updated = MigrationManifest(
        migration_id=manifest.migration_id,
        unit=manifest.unit,
        source=manifest.source,
        target=manifest.target,
        source_hash=manifest.source_hash,
        target_hash=manifest.target_hash,
        dependencies=manifest.dependencies,
        state=to_state,
        parity=manifest.parity,
        shadow=manifest.shadow,
        integration=manifest.integration,
        authority=manifest.authority,
        removal=manifest.removal,
        evidence=manifest.evidence,
        live_critical=manifest.live_critical,
        version=manifest.version,
    )
    return append_evidence(updated, f"{_stamp()} ADVANCE {manifest.state}->{to_state}: {reason}")


def _set_authority(manifest: MigrationManifest, authority: str, reason: str) -> MigrationManifest:
    if manifest.authority == authority:
        return manifest
    updated = MigrationManifest(
        migration_id=manifest.migration_id,
        unit=manifest.unit,
        source=manifest.source,
        target=manifest.target,
        source_hash=manifest.source_hash,
        target_hash=manifest.target_hash,
        dependencies=manifest.dependencies,
        state=manifest.state,
        parity=manifest.parity,
        shadow=manifest.shadow,
        integration=manifest.integration,
        authority=authority,
        removal=manifest.removal,
        evidence=manifest.evidence,
        live_critical=manifest.live_critical,
        version=manifest.version,
    )
    return append_evidence(updated, f"{_stamp()} AUTHORITY {authority}: {reason}")


def verify(shadow_count: int = 60) -> dict:
    """Run full verification and persist every artifact. Returns a summary."""
    from pathlib import Path as _Path

    _Path(INVENTORY_PATH).parent.mkdir(parents=True, exist_ok=True)
    inv = scan()
    INVENTORY_PATH.write_text(
        json.dumps(inventory_dict(inv), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    graph = build_graph()
    GRAPH_PATH.write_text(
        json.dumps(graph_dict(graph), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    golden_written = persist_golden()

    manifests = load_all()

    parity_outcomes = {o.unit_id: o for o in run_all_parity()}
    shadow_outcomes = {o.unit_id: o for o in run_all_shadow(shadow_count)}

    for unit_id, manifest in list(manifests.items()):
        outcome = parity_outcomes.get(unit_id)
        shadow = shadow_outcomes.get(unit_id)
        if outcome is None and shadow is None:
            continue  # no harness for this unit: nothing to re-verify
        harness_broken = (
            outcome is None
            or outcome.verdict == "INCONCLUSIVE"
            or shadow is None
            or shadow.verdict == "INCONCLUSIVE"
        )
        if harness_broken:
            # No fresh evidence: never re-pin hashes (a changed file must
            # surface as STALE, an unchanged one keeps its PASS).
            manifests[unit_id] = append_evidence(
                manifest, f"{_stamp()} HARNESS UNAVAILABLE: kept pinned evidence"
            )
            continue
        manifest = refresh_hashes(manifest)
        assert outcome is not None and shadow is not None
        parity_status = "PASS" if outcome.verdict == "PASS" else "FAIL"
        manifests[unit_id] = MigrationManifest(
            migration_id=manifest.migration_id,
            unit=manifest.unit,
            source=manifest.source,
            target=manifest.target,
            source_hash=manifest.source_hash,
            target_hash=manifest.target_hash,
            dependencies=manifest.dependencies,
            state=manifest.state,
            parity=ParitySummary(parity_status, outcome.cases, outcome.passed, outcome.failed),
            shadow=manifest.shadow,
            integration=manifest.integration,
            authority=manifest.authority,
            removal=manifest.removal,
            evidence=manifest.evidence,
            live_critical=manifest.live_critical,
            version=manifest.version,
        )
        manifests[unit_id] = append_evidence(
            manifests[unit_id],
            f"{_stamp()} PARITY {outcome.verdict}: "
            f"{outcome.passed}/{outcome.cases} passed"
            + (f" mismatches={outcome.mismatches[:3]}" if outcome.mismatches else ""),
        )
        manifest = manifests[unit_id]
        sufficient = shadow.comparisons >= MIN_SHADOW_COMPARISONS
        if shadow.verdict == "PASS" and sufficient:
            shadow_status = "PASS"
        elif shadow.verdict == "FAIL":
            shadow_status = "FAIL"
        else:
            shadow_status = "INCONCLUSIVE"
        manifests[unit_id] = MigrationManifest(
            migration_id=manifest.migration_id,
            unit=manifest.unit,
            source=manifest.source,
            target=manifest.target,
            source_hash=manifest.source_hash,
            target_hash=manifest.target_hash,
            dependencies=manifest.dependencies,
            state=manifest.state,
            parity=manifest.parity,
            shadow=ShadowSummary(shadow_status, shadow.comparisons, shadow.mismatches),
            integration=manifest.integration,
            authority=manifest.authority,
            removal=manifest.removal,
            evidence=manifest.evidence,
            live_critical=manifest.live_critical,
            version=manifest.version,
        )
        manifests[unit_id] = append_evidence(
            manifests[unit_id],
            f"{_stamp()} SHADOW {shadow.verdict}: "
            f"{shadow.comparisons} comparisons, {shadow.mismatches} mismatches",
        )

    for unit_id, manifest in list(manifests.items()):
        uses_rust, detail = production_uses_rust(unit_id)
        live_source, live_target = current_hashes(manifest)
        fresh = manifest.source_hash == live_source and manifest.target_hash == live_target
        integration = IntegrationSummary(
            status="PASS" if uses_rust and fresh else "INCONCLUSIVE",
            production_uses_rust=uses_rust,
            python_still_authoritative=not uses_rust,
            detail=detail,
        )
        manifests[unit_id] = MigrationManifest(
            migration_id=manifest.migration_id,
            unit=manifest.unit,
            source=manifest.source,
            target=manifest.target,
            source_hash=manifest.source_hash,
            target_hash=manifest.target_hash,
            dependencies=manifest.dependencies,
            state=manifest.state,
            parity=manifest.parity,
            shadow=manifest.shadow,
            integration=integration,
            authority=manifest.authority,
            removal=manifest.removal,
            evidence=manifest.evidence,
            live_critical=manifest.live_critical,
            version=manifest.version,
        )

    # Fixed-point state advancement along permitted edges only.
    # Fixed-point advancement: longest path BLOCKED→…→INTEGRATED needs 7
    # edges; 10 passes leave headroom without unbounded looping.
    for _ in range(10):
        moved = False
        states = {uid: m.state for uid, m in manifests.items()}
        for unit_id, manifest in list(manifests.items()):
            target_state = _next_state(manifest, states)
            if target_state and is_allowed(manifest.state, target_state):
                manifests[unit_id] = _advance(manifest, target_state, _advance_reason(target_state))
                if target_state == "INTEGRATED":
                    manifests[unit_id] = _set_authority(
                        manifests[unit_id], "rust", "production path proven through Rust bridge"
                    )
                moved = True
                states[unit_id] = target_state
        if not moved:
            break

    for manifest in manifests.values():
        save_manifest(manifest)
    return {
        "units": len(manifests),
        "golden_files": len(golden_written),
        "states": {uid: m.state for uid, m in sorted(manifests.items())},
    }


def _rust_target_exists(manifest: MigrationManifest) -> bool:
    from .config import ROOT

    return bool(manifest.target) and (ROOT / manifest.target).is_file()


def _next_state(manifest: MigrationManifest, states: dict[str, str]) -> str | None:
    from .validator import _has_duplicate_table

    state = manifest.state
    if state == "REGRESSION":
        # Recovery is evidence-driven too: fresh parity PASS re-opens the
        # implementation path instead of silently resuming canonical status.
        if manifest.parity.status == "PASS" and _rust_target_exists(manifest):
            return "IMPLEMENTING"
        return None
    if state == "DISCOVERED":
        return "ANALYZED"
    if state == "ANALYZED":
        return "PLANNED"
    if state == "PLANNED":
        if not _rust_target_exists(manifest):
            return "BLOCKED"
        if blockers_for(manifest.unit, states):
            return "BLOCKED"
        return "READY"
    if state == "BLOCKED":
        if _rust_target_exists(manifest) and not blockers_for(manifest.unit, states):
            return "READY"
        return None
    if state == "READY":
        return "IMPLEMENTING" if _rust_target_exists(manifest) else None
    if state == "IMPLEMENTING":
        if _rust_target_exists(manifest) and not _has_duplicate_table(manifest.unit):
            return "IMPLEMENTED"
        return None
    if state == "IMPLEMENTED":
        return "PARITY_TESTING"
    if state == "PARITY_TESTING":
        if manifest.parity.status == "PASS":
            return "PARITY_VERIFIED"
        return None
    if state == "PARITY_VERIFIED":
        if manifest.shadow.status == "PASS":
            return "SHADOW_VALIDATED"
        if manifest.shadow.status == "FAIL":
            return "BLOCKED"
        return None
    if state == "SHADOW_VALIDATED":
        if manifest.integration.production_uses_rust and manifest.parity.status == "PASS":
            return "INTEGRATED"
        return None
    return None


def _advance_reason(state: str) -> str:
    reasons = {
        "ANALYZED": "inventory lists unit with sources and dependencies",
        "PLANNED": "dependencies resolved from registry",
        "READY": "dependencies migrated or glue; Rust target exists",
        "BLOCKED": "waiting on Rust target or dependencies",
        "IMPLEMENTING": "Rust target exists",
        "IMPLEMENTED": "Rust target exists; no Python duplicate table",
        "PARITY_TESTING": "parity harness executed",
        "PARITY_VERIFIED": "parity PASS on fresh hashes",
        "SHADOW_VALIDATED": "shadow PASS on fresh hashes",
        "INTEGRATED": "production path proven through Rust bridge",
    }
    return reasons.get(state, "evidence satisfied")


__all__ = ["verify"]
