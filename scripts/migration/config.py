"""Shared paths, tolerances and policy constants for the migration system.

Stdlib only. All generated state lives under ``90_brain/migration/`` so the
product chapters and ``rust/`` stay untouched by the tooling.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
BRAIN = ROOT / "90_brain"
MIGRATION_DIR = BRAIN / "migration"
MANIFESTS_DIR = MIGRATION_DIR / "manifests"
GOLDEN_DIR = MIGRATION_DIR / "golden"
SHADOW_DIR = MIGRATION_DIR / "shadow"
EVIDENCE_DIR = MIGRATION_DIR / "evidence"
INVENTORY_PATH = MIGRATION_DIR / "inventory.json"
GRAPH_PATH = MIGRATION_DIR / "graph.json"
REPORT_JSON_PATH = MIGRATION_DIR / "report.json"
REPORT_MD_PATH = MIGRATION_DIR / "report.md"

OWNERSHIP_POLICY_PATH = BRAIN / "ownership_policy.json"
RETENTION_PATH = BRAIN / "language_retention.json"

RUST_WORKSPACE = ROOT / "rust"
RUST_CORE_SRC = RUST_WORKSPACE / "vayren-core" / "src"

# Relative float tolerance for parity comparisons (explicit, never exact
# equality on floats). Absolute floor covers near-zero magnitudes.
REL_TOLERANCE = 1e-9
ABS_TOLERANCE = 1e-12

# Minimum golden cases per category before parity may be called verified.
MIN_GOLDEN_PER_CATEGORY = 2

# Minimum shadow comparisons before shadow may be called validated.
MIN_SHADOW_COMPARISONS = 50

# Maximum shadow mismatches tolerated for promotion (zero for live paths).
MAX_SHADOW_MISMATCHES = 0

# Deterministic seeds so every run reproduces the same fixtures.
GOLDEN_SEED = 20260911
FUZZ_SEED = 20260907

# Units whose promotion additionally requires the live-trading safety gate
# (deterministic + integration + dry-run + shadow + explicit promotion).
LIVE_CRITICAL_PREFIXES = ("execution.", "risk.", "broker.")

# File patterns whose content must never appear in manifests, fixtures,
# logs, reports or telemetry.
SECRET_PATTERNS = (
    "api_key",
    "api_secret",
    "access_token",
    "request_token",
    "secret",
    "password",
    "session_token",
    "client_secret",
    "private_key",
)

SECRET_REDACTION = "***REDACTED***"

MIGRATION_VERSION = 1

__all__ = [
    "ROOT",
    "BRAIN",
    "MIGRATION_DIR",
    "MANIFESTS_DIR",
    "GOLDEN_DIR",
    "SHADOW_DIR",
    "EVIDENCE_DIR",
    "INVENTORY_PATH",
    "GRAPH_PATH",
    "REPORT_JSON_PATH",
    "REPORT_MD_PATH",
    "OWNERSHIP_POLICY_PATH",
    "RETENTION_PATH",
    "RUST_WORKSPACE",
    "RUST_CORE_SRC",
    "REL_TOLERANCE",
    "ABS_TOLERANCE",
    "MIN_GOLDEN_PER_CATEGORY",
    "MIN_SHADOW_COMPARISONS",
    "MAX_SHADOW_MISMATCHES",
    "GOLDEN_SEED",
    "FUZZ_SEED",
    "LIVE_CRITICAL_PREFIXES",
    "SECRET_PATTERNS",
    "SECRET_REDACTION",
    "MIGRATION_VERSION",
]
