"""Automatic cutover: promote Rust from candidate to canonical authority.

Before cutover Python is canonical and Rust is the candidate; after cutover
Rust is canonical and Python is deprecated/shadow-only. Promotion is explicit
and auditable: it requires fresh parity, fresh shadow, proven production use
of the Rust path and the live-trading safety gate where applicable.
"""

from __future__ import annotations

import time

from .manifest_store import append_evidence, load_all, refresh_hashes, save_manifest
from .models import MigrationManifest
from .validator import check_unit


def _stamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def promote(unit_id: str, actor: str = "migration-cli") -> tuple[bool, str]:
    """Promote a unit to RUST_CANONICAL after verifying every gate."""
    manifests = load_all()
    manifest = manifests.get(unit_id)
    if manifest is None:
        return False, f"unknown unit: {unit_id}"
    if manifest.state != "INTEGRATED":
        return False, (
            f"promotion refused for {unit_id}: state is {manifest.state}, "
            f"must be INTEGRATED (run: migration verify)"
        )
    verdict, reasons = check_unit(manifest, manifests)
    blocking = [r for r in reasons if "promotion" in r.lower() or verdict in ("FAIL", "STALE")]
    gate_ok = verdict == "PASS" and manifest.parity.status == "PASS"
    gate_ok = gate_ok and manifest.shadow.status == "PASS"
    gate_ok = gate_ok and manifest.integration.production_uses_rust
    if manifest.live_critical and "live-gate" in " ".join(reasons).lower():
        return False, "live-critical unit refused: live safety gate not satisfied: " + "; ".join(
            reasons
        )
    if not gate_ok:
        detail = "; ".join(blocking or reasons) or "promotion gates not satisfied"
        return False, f"promotion refused for {unit_id}: {detail}"
    updated = refresh_hashes(manifest)
    updated = MigrationManifest(
        migration_id=updated.migration_id,
        unit=updated.unit,
        source=updated.source,
        target=updated.target,
        source_hash=updated.source_hash,
        target_hash=updated.target_hash,
        dependencies=updated.dependencies,
        state="RUST_CANONICAL",
        parity=updated.parity,
        shadow=updated.shadow,
        integration=updated.integration,
        authority="rust",
        removal=updated.removal,
        evidence=updated.evidence,
        live_critical=updated.live_critical,
        version=updated.version,
    )
    updated = append_evidence(updated, f"{_stamp()} PROMOTE rust-canonical by {actor}")
    save_manifest(updated)
    return True, f"{unit_id} promoted: authority rust (RUST_CANONICAL)"


__all__ = ["promote"]
