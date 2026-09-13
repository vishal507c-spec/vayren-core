"""Automatic rollback: Rust canonical -> failure -> Python canonical.

Migration is reversible. A critical post-cutover regression restores Python
as canonical, preserves the failure evidence for reproduction and marks the
unit REGRESSION/BLOCKED. Rollback never deletes evidence.
"""

from __future__ import annotations

import time

from .manifest_store import append_evidence, load_all, save_manifest
from .models import MigrationManifest
from .redact import redact_text


def _stamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def rollback(unit_id: str, reason: str, actor: str = "migration-cli") -> tuple[bool, str]:
    """Roll back a canonical unit to Python authority, preserving evidence."""
    manifests = load_all()
    manifest = manifests.get(unit_id)
    if manifest is None:
        return False, f"unknown unit: {unit_id}"
    clean_reason = redact_text(reason or "unspecified regression")
    if manifest.authority != "rust":
        return False, f"rollback refused: {unit_id} authority is {manifest.authority}, not rust"
    updated = MigrationManifest(
        migration_id=manifest.migration_id,
        unit=manifest.unit,
        source=manifest.source,
        target=manifest.target,
        source_hash=manifest.source_hash,
        target_hash=manifest.target_hash,
        dependencies=manifest.dependencies,
        state="REGRESSION",
        parity=manifest.parity,
        shadow=manifest.shadow,
        integration=manifest.integration,
        authority="python",
        removal=manifest.removal,
        evidence=manifest.evidence,
        live_critical=manifest.live_critical,
        version=manifest.version,
    )
    updated = append_evidence(updated, f"{_stamp()} ROLLBACK by {actor}: {clean_reason}")
    save_manifest(updated)
    return True, f"{unit_id} rolled back: authority python (REGRESSION): {clean_reason}"


__all__ = ["rollback"]
