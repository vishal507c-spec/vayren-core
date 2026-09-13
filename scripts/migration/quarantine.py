"""Python quarantine: canonical Rust with the old Python kept for shadow only.

A quarantined implementation cannot be production authority, cannot be
imported by production paths and cannot silently become active. Any attempt
to reintroduce it is detected and reported as a hard failure.
"""

from __future__ import annotations

import ast
from pathlib import Path

from .config import ROOT
from .manifest_store import append_evidence, load_all, save_manifest
from .models import MigrationManifest
from .scanner import _is_test_path

QUARANTINE_STATES = ("PYTHON_QUARANTINED", "PYTHON_REMOVED", "FINAL_VERIFIED", "MIGRATED")


def production_importers_of(source: str) -> list[str]:
    """Non-test files whose AST imports the quarantined module stem."""
    stem = Path(source).stem
    importers: list[str] = []
    for path in sorted(ROOT.rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        if _is_test_path(rel) or rel.startswith("scripts/migration/"):
            continue
        if "90_brain" in rel or "99_archive" in rel:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            if any(stem in name for name in names):
                importers.append(rel)
                break
    return importers


def check_quarantine(manifest: MigrationManifest) -> tuple[bool, str]:
    """True when a quarantined unit stays out of production imports."""
    if manifest.state not in QUARANTINE_STATES:
        return True, "not quarantined"
    importers = production_importers_of(manifest.source)
    # The unit's own source file existing is expected during quarantine;
    # other production importers are violations.
    others = [rel for rel in importers if rel != manifest.source]
    if others:
        return False, f"quarantined {manifest.source} reimported by: {', '.join(others[:5])}"
    return True, "quarantine intact"


def quarantine(unit_id: str, actor: str = "migration-cli") -> tuple[bool, str]:
    manifests = load_all()
    manifest = manifests.get(unit_id)
    if manifest is None:
        return False, f"unknown unit: {unit_id}"
    if manifest.authority != "rust":
        return False, f"quarantine refused: {unit_id} authority is {manifest.authority}"
    ok, detail = check_quarantine(manifest)
    if not ok:
        return False, f"quarantine refused: {detail}"
    updated = MigrationManifest(
        migration_id=manifest.migration_id,
        unit=manifest.unit,
        source=manifest.source,
        target=manifest.target,
        source_hash=manifest.source_hash,
        target_hash=manifest.target_hash,
        dependencies=manifest.dependencies,
        state="PYTHON_QUARANTINED",
        parity=manifest.parity,
        shadow=manifest.shadow,
        integration=manifest.integration,
        authority="rust",
        removal=manifest.removal,
        evidence=manifest.evidence,
        live_critical=manifest.live_critical,
        version=manifest.version,
    )
    updated = append_evidence(updated, f"quarantined by {actor}: {detail}")
    save_manifest(updated)
    return True, f"{unit_id} quarantined: python shadow-only"


__all__ = ["production_importers_of", "check_quarantine", "quarantine", "QUARANTINE_STATES"]
