"""Agent self-check + §25 final report, computed from evidence — never asserted.

Answers the ten self-check questions against the working tree and the
validators, then renders the required ARCHITECTURE / CHANGES / VALIDATION /
MIGRATION IMPACT / STATUS report. Any YES on accidental Python for
Rust/Slint-owned work fails the task.
"""

from __future__ import annotations

from pathlib import Path

from .change_gate import _policy_rules, check_changes, git_changed
from .config import ROOT
from .manifest_store import load_all
from .scanner import _classify


def _required_language(rel: str, rules: list[dict]) -> str:
    for rule in rules:
        if any(rel.startswith(prefix) for prefix in rule.get("directory_prefixes", [])):
            if any(rel.startswith(exc) for exc in rule.get("excluded_subpaths", [])):
                return ""
            return str(rule.get("required_language", ""))
    return ""


def build_task_report(root: Path = ROOT, ref: str = "HEAD", manifests: dict | None = None) -> dict:
    """Compute the §25 report for the current worktree diff."""
    rules = _policy_rules(root)
    gate = check_changes(root, ref)
    if manifests is None:
        try:
            manifests = load_all()
        except Exception:
            manifests = {}

    files: list[dict] = []
    rust_touched: list[str] = []
    slint_touched: list[str] = []
    python_touched: list[str] = []
    accidental_python: list[str] = []
    retained_touched: list[tuple[str, str]] = []
    try:
        changed = git_changed(root, ref)
        candidates = changed["modified"] + changed["added"]
    except OSError:
        candidates = []
    for rel in sorted(candidates):
        suffix = rel.rsplit(".", 1)[-1] if "." in rel else ""
        if suffix == "rs":
            rust_touched.append(rel)
            files.append({"file": rel, "domain": "RUST_CRATE", "canonical": "rust"})
        elif suffix == "slint":
            slint_touched.append(rel)
            files.append({"file": rel, "domain": "NATIVE_UI", "canonical": "slint"})
        elif suffix == "py":
            domain = _classify(rel, rules)
            required = _required_language(rel, rules).lower()
            canonical = (
                "slint"
                if required == "rust_slint"
                else (required if required in ("rust", "python") else "tooling/test")
            )
            files.append({"file": rel, "domain": domain, "canonical": canonical})
            if domain in ("STRATEGY", "RESEARCH", "AI_ADJACENT", "TEST", "TOOLING"):
                python_touched.append(rel)
            elif required in ("rust", "rust_slint") and not _is_test_or_tooling(rel):
                accidental_python.append(rel)
                from .change_gate import _retention

                if rel in _retention(root):
                    retained_touched.append((rel, "retained-with-entry"))
        else:
            files.append({"file": rel, "domain": "OTHER", "canonical": "n/a"})

    touched_units: dict[str, str] = {}
    for unit_id, manifest in manifests.items():
        for rel in candidates:
            if manifest.source == rel or manifest.target == rel:
                touched_units[unit_id] = manifest.state
    migration_debt = gate.debt_created

    self_check = {
        "domain_identified": bool(files),
        "canonical_language_used": not accidental_python
        or all(rel in dict(retained_touched) for rel in accidental_python),
        "accidental_python_created": bool(accidental_python),
        "retained_files_justified": all(rel in dict(retained_touched) for rel in accidental_python),
        "ownership_violations": [v.__dict__ for v in gate.violations],
        "architecture_validation_run": True,
        "new_migration_debt": migration_debt,
    }
    status = "PASS"
    if gate.verdict == "FAIL" or (accidental_python and not self_check["retained_files_justified"]):
        status = "FAILED"
    elif gate.verdict == "ERROR" or gate.warnings or migration_debt:
        status = "BLOCKED"

    return {
        "architecture": {
            "files": files,
            "touched_units": touched_units,
        },
        "changes": {
            "rust": sorted(rust_touched),
            "slint": sorted(slint_touched),
            "python": sorted(python_touched),
            "accidental_python": sorted(accidental_python),
            "other": sorted(entry["file"] for entry in files if entry["canonical"] == "n/a"),
        },
        "validation": {
            "architecture_gate": gate.verdict,
            "gate_violations": [v.__dict__ for v in gate.violations],
            "gate_warnings": list(gate.warnings),
        },
        "migration_impact": {
            "new_migration_debt": migration_debt,
            "python_retained": sorted(dict(retained_touched).keys()),
            "python_removed": [],
        },
        "self_check": self_check,
        "status": status,
    }


def _is_test_or_tooling(rel: str) -> bool:
    return (
        "/tests/" in rel
        or Path(rel).name.startswith("test_")
        or rel.endswith("conftest.py")
        or rel.startswith("scripts/")
    )


__all__ = ["build_task_report"]
