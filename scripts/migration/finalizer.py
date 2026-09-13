"""Automatic Python removal: delete only when every safety condition holds.

Required conditions: dependency graph clean, no production import path, no
authoritative runtime reference, parity verified fresh, shadow completed,
integration passed, regression suite evidence, Rust canonical, no unresolved
downstream dependency, migration evidence recorded. Never a manual flag flip.
"""

from __future__ import annotations

from .graph import blockers_for, build_graph
from .manifest_store import append_evidence, load_all, save_manifest
from .models import MigrationManifest
from .quarantine import check_quarantine


def removal_readiness(
    manifest: MigrationManifest, states: dict[str, str]
) -> tuple[bool, list[str]]:
    missing: list[str] = []
    if manifest.authority != "rust":
        missing.append("rust is not canonical")
    if manifest.parity.status != "PASS":
        missing.append("parity not verified fresh")
    if manifest.shadow.status != "PASS":
        missing.append("shadow validation not completed")
    if not manifest.integration.production_uses_rust:
        missing.append("production path does not use Rust")
    if manifest.integration.python_still_authoritative:
        missing.append("python still authoritative at runtime")
    ok, detail = check_quarantine(manifest)
    if not ok:
        missing.append(detail)
    graph = build_graph()
    blockers = blockers_for(manifest.unit, states, graph)
    if blockers:
        missing.append(f"downstream dependencies unresolved: {', '.join(blockers)}")
    dependents = graph.get(manifest.unit)
    if dependents and dependents.dependents:
        pending = [d for d in dependents.dependents if states.get(d) == "REGRESSION"]
        if pending:
            missing.append(f"dependents in regression: {', '.join(pending)}")
    if not manifest.evidence:
        missing.append("migration evidence not recorded")
    return (not missing, missing)


def finalize(unit_id: str, actor: str = "migration-cli") -> tuple[bool, str]:
    manifests = load_all()
    manifest = manifests.get(unit_id)
    if manifest is None:
        return False, f"unknown unit: {unit_id}"
    states = {uid: m.state for uid, m in manifests.items()}
    ready, missing = removal_readiness(manifest, states)
    if not ready:
        return False, f"finalize refused for {unit_id}: " + "; ".join(missing)
    updated = MigrationManifest(
        migration_id=manifest.migration_id,
        unit=manifest.unit,
        source=manifest.source,
        target=manifest.target,
        source_hash=manifest.source_hash,
        target_hash=manifest.target_hash,
        dependencies=manifest.dependencies,
        state="FINAL_VERIFIED",
        parity=manifest.parity,
        shadow=manifest.shadow,
        integration=manifest.integration,
        authority="rust",
        removal=manifest.removal,
        evidence=manifest.evidence,
        live_critical=manifest.live_critical,
        version=manifest.version,
    )
    updated = append_evidence(updated, f"removal safety verified by {actor}")
    save_manifest(updated)
    return True, f"{unit_id} finalized: safe to remove Python (FINAL_VERIFIED)"


__all__ = ["removal_readiness", "finalize"]
