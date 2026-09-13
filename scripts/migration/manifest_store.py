"""Manifest persistence: one JSON file per migration unit.

Manifests are machine-generated and machine-checked. They pin source/target
hashes so stale evidence is detectable, and they record the ordered evidence
trail that justifies every state advance.
"""

from __future__ import annotations

import json
from pathlib import Path

from .config import MANIFESTS_DIR, MIGRATION_VERSION
from .hashing import hash_paths
from .models import (
    IntegrationSummary,
    MigrationManifest,
    ParitySummary,
    RemovalSummary,
    ShadowSummary,
)
from .redact import redact_text
from .registry import seed_units


def manifest_path(unit_id: str) -> Path:
    safe = unit_id.replace(".", "_").replace("/", "_")
    return MANIFESTS_DIR / f"{safe}.json"


def _seed_manifest(unit_id: str) -> MigrationManifest | None:
    for unit in seed_units():
        if unit.unit_id == unit_id:
            return MigrationManifest(
                migration_id=f"vayren-{unit_id}-v{MIGRATION_VERSION}",
                unit=unit_id,
                source=unit.python_source,
                target=unit.rust_target,
                dependencies=unit.dependencies,
                live_critical=unit.live_critical,
                version=MIGRATION_VERSION,
            )
    return None


def _parse(data: dict) -> MigrationManifest:
    parity = data.get("parity", {})
    shadow = data.get("shadow", {})
    integration = data.get("integration", {})
    removal = data.get("removal", {})
    return MigrationManifest(
        migration_id=str(data.get("migration_id", "")),
        unit=str(data.get("unit", "")),
        source=str(data.get("source", "")),
        target=str(data.get("target", "")),
        source_hash=str(data.get("source_hash", "")),
        target_hash=str(data.get("target_hash", "")),
        dependencies=tuple(data.get("dependencies", [])),
        state=str(data.get("state", "DISCOVERED")),
        parity=ParitySummary(
            status=str(parity.get("status", "INCONCLUSIVE")),
            cases=int(parity.get("cases", 0)),
            passed=int(parity.get("passed", 0)),
            failed=int(parity.get("failed", 0)),
        ),
        shadow=ShadowSummary(
            status=str(shadow.get("status", "INCONCLUSIVE")),
            comparisons=int(shadow.get("comparisons", 0)),
            mismatches=int(shadow.get("mismatches", 0)),
        ),
        integration=IntegrationSummary(
            status=str(integration.get("status", "INCONCLUSIVE")),
            production_uses_rust=bool(integration.get("production_uses_rust", False)),
            python_still_authoritative=bool(integration.get("python_still_authoritative", True)),
        ),
        authority=str(data.get("authority", "python")),
        removal=RemovalSummary(status=str(removal.get("status", "INCONCLUSIVE"))),
        evidence=tuple(data.get("evidence", [])),
        live_critical=bool(data.get("live_critical", False)),
        version=int(data.get("version", MIGRATION_VERSION)),
    )


def load_manifest(unit_id: str) -> MigrationManifest | None:
    path = manifest_path(unit_id)
    if not path.is_file():
        return None
    try:
        return _parse(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return None


def load_all() -> dict[str, MigrationManifest]:
    out: dict[str, MigrationManifest] = {}
    if MANIFESTS_DIR.is_dir():
        for path in sorted(MANIFESTS_DIR.glob("*.json")):
            try:
                manifest = _parse(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
            out[manifest.unit] = manifest
    for unit in seed_units():
        if unit.unit_id not in out:
            seed = _seed_manifest(unit.unit_id)
            if seed is not None:
                out[unit.unit_id] = seed
    return out


def save_manifest(manifest: MigrationManifest) -> Path:
    MANIFESTS_DIR.mkdir(parents=True, exist_ok=True)
    path = manifest_path(manifest.unit)
    payload = redact_text(json.dumps(manifest.to_dict(), indent=2, sort_keys=True))
    path.write_text(payload + "\n", encoding="utf-8")
    return path


def refresh_hashes(manifest: MigrationManifest) -> MigrationManifest:
    """Re-pin hashes to current file contents (invalidates old evidence)."""
    source_hash = hash_paths([manifest.source]) if manifest.source else "none"
    target_hash = hash_paths([manifest.target]) if manifest.target else "none"
    return MigrationManifest(
        migration_id=manifest.migration_id,
        unit=manifest.unit,
        source=manifest.source,
        target=manifest.target,
        source_hash=source_hash,
        target_hash=target_hash,
        dependencies=manifest.dependencies,
        state=manifest.state,
        parity=manifest.parity,
        shadow=manifest.shadow,
        integration=manifest.integration,
        authority=manifest.authority,
        removal=manifest.removal,
        evidence=manifest.evidence,
        live_critical=manifest.live_critical,
        version=manifest.version,
    )


def append_evidence(manifest: MigrationManifest, entry: str) -> MigrationManifest:
    clean = redact_text(entry)
    return MigrationManifest(
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
        authority=manifest.authority,
        removal=manifest.removal,
        evidence=manifest.evidence + (clean,),
        live_critical=manifest.live_critical,
        version=manifest.version,
    )


__all__ = [
    "manifest_path",
    "load_manifest",
    "load_all",
    "save_manifest",
    "refresh_hashes",
    "append_evidence",
]
