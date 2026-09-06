"""Context efficiency, fingerprints, stale-cache guard, context compiler.

Proxy metrics only (§7): whatever is counted here comes from explicitly
recorded reads/searches/edits. Token/byte usage is unavailable and is NOT
estimated — fields that cannot be measured are simply absent.
"""

from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

MAX_FILES = 25
MAX_PLANS = 3

# Static, versioned project facts. Provenance is always reported as "static"
# so generated output never poses them as fresh measurements (§9: reuse only
# where safe — these change only with the architecture docs).
KNOWN_CONSTRAINTS: tuple[str, ...] = (
    "Event-driven only: modules communicate via EventBus.",
    "Subscriptions are created only in 00_app/app/bootstrap/bootstrap.py.",
    "Cross-module imports use only module __init__.py public surfaces.",
    "No relative cross-module imports, no star imports.",
    "Widgets never touch EventBus/SQL; painting only in renderer.",
    "No placeholder, mock or sample trading logic; no TODO/FIXME.",
)

VALIDATORS: tuple[str, ...] = (
    "ruff check",
    "ruff format --check",
    "pyright",
    "pytest (impact-first, then full gate)",
    "python scripts/validate_structure.py",
    "python scripts/validate_imports.py",
)

# Mirrors benchmark._domain_of (path NN_chapter/domain/... -> domain).
_DOMAINS = ("core", "data", "market", "chart", "strategy", "backtest", "risk", "execution", "app")


def _domain_of(path: str) -> str | None:
    parts = path.replace("\\", "/").split("/")
    if len(parts) >= 2 and parts[1] in _DOMAINS:
        return parts[1]
    return None


def _benchmark() -> object:
    """Lazy import of the benchmark harness (stdlib, no side effects)."""
    scripts_dir = str(Path(__file__).resolve().parent.parent)
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    import benchmark

    return benchmark


def normalize_spec(spec: str) -> str:
    """Canonicalize a task description so equivalent tasks fingerprint equal."""
    collapsed = re.sub(r"\s+", " ", (spec or "").strip().lower())
    return re.sub(r"[^a-z0-9 ]", "", collapsed)


def fingerprint(task_class: str, spec: str, files: tuple[str, ...] = ()) -> str:
    """Stable identity for equivalent tasks (§8, step 1).

    Same class + same normalized request + same file set => same print.
    Unrelated tasks never share one (§5).
    """
    canonical = "\n".join([task_class, normalize_spec(spec), *sorted(files)])
    return hashlib.sha1(canonical.encode("utf-8")).hexdigest()[:12]


def is_stale(
    record: dict,
    *,
    revision: str | None = None,
    mtimes: dict[str, float] | None = None,
) -> bool | None:
    """True when cached knowledge must be invalidated (§8).

    Compares the current revision/mtimes against the record baseline.
    Returns None when the record carries no baseline to compare against —
    unknown freshness is reported, never assumed fresh or stale.
    """
    baseline_revision = record.get("revision")
    baseline_files = record.get("files") or record.get("file_baseline")
    if revision is not None and baseline_revision is not None and revision != baseline_revision:
        return True
    if mtimes is not None and isinstance(baseline_files, dict) and baseline_files:
        for name, old_mtime in baseline_files.items():
            new_mtime = mtimes.get(name)
            if new_mtime is None or float(new_mtime) != float(old_mtime):
                return True
        return False  # every tracked file matches: fresh by file evidence
    if revision is not None and baseline_revision is not None:
        return False
    return None


def compile_context(
    request: str,
    *,
    task_class: str = "UNCLASSIFIED",
    files: tuple[str, ...] = (),
    journal_records: list[dict] | None = None,
    revision: str | None = None,
    max_files: int = MAX_FILES,
    max_plans: int = MAX_PLANS,
) -> dict:
    """Build the minimal relevant context package for a request (§10).

    Output: affected domains, relevant files (capped), tests, validators,
    historical successful plans (fresh only — stale entries are excluded
    and counted), known constraints. LESS context, HIGHER relevance: lists
    are capped and every entry carries provenance.
    """
    bench = _benchmark()
    domain_tests = getattr(bench, "DOMAIN_TESTS", {})
    impact_plan = getattr(bench, "impact_plan", None)
    domains = sorted({d for d in (_domain_of(f) for f in files) if d is not None})
    kept_files = list(files[:max_files])
    tests: list[str] = []
    for domain in domains:
        tests.extend(str(domain_tests.get(domain, "")).split())
    tests = sorted({t for t in tests if t})
    # Impact-first validation plan (§11): reuse the proven benchmark planner
    # instead of re-deriving it — likely tests, validators and risk areas.
    validation_plan: dict = {"level": None, "reason": "UNMEASURED"}
    if callable(impact_plan):
        try:
            planned = impact_plan(list(kept_files))
            if isinstance(planned, dict):
                validation_plan = planned
        except Exception:
            validation_plan = {"level": None, "reason": "UNMEASURED (planner error)"}
    plans: list[dict] = []
    stale_excluded = 0
    for record in journal_records or []:
        if record.get("kind") != "speed-task":
            continue
        if task_class != "UNCLASSIFIED" and record.get("task_class") != task_class:
            continue
        if record.get("repairs") not in (0, None):
            continue  # only proven clean plans are reusable
        stale = is_stale(record, revision=revision)
        if stale:
            stale_excluded += 1
            continue
        plans.append(
            {
                "task": record.get("task"),
                "fingerprint": record.get("fingerprint"),
                "validation_ref": record.get("validation_ref"),
                "notes": record.get("notes"),
                "provenance": "speed-journal",
            }
        )
        if len(plans) >= max_plans:
            break
    return {
        "fingerprint": fingerprint(task_class, request, tuple(kept_files)),
        "task_class": task_class,
        "domains": domains,
        "files": kept_files,
        "truncated_files": max(0, len(files) - len(kept_files)),
        "tests": tests,
        "validators": list(VALIDATORS),
        "validation_plan": validation_plan,
        "plans": plans,
        "stale_excluded": stale_excluded,
        "constraints": list(KNOWN_CONSTRAINTS),
        "constraint_provenance": "static",
    }


def proxy_metrics(
    files_read: tuple[str, ...] = (),
    *,
    searches: int = 0,
    files_edited: tuple[str, ...] = (),
    files_unchanged: tuple[str, ...] = (),
) -> dict:
    """Proxy context-efficiency metrics (§7) — counts only, labeled proxies."""
    inspected = len(files_read)
    unique = len(set(files_read))
    duplicate_reads = inspected - unique
    reread = sum(1 for name in set(files_read) if files_read.count(name) > 1)
    return {
        "files_inspected": inspected,
        "files_unique": unique,
        "files_reread": reread,
        "duplicate_reads": duplicate_reads,
        "duplicate_read_rate": round(duplicate_reads / inspected, 3) if inspected else None,
        "search_count": searches,
        "files_edited": len(files_edited),
        "files_unchanged": len(files_unchanged),
        "proxy": True,
    }


def complexity_signals(
    *,
    files_changed: int = 0,
    lines_added: int = 0,
    lines_removed: int = 0,
    domains: tuple[str, ...] = (),
    tests_affected: int = 0,
    public_surface: bool = False,
    multi_domain: bool = False,
) -> dict:
    """Objective task-complexity signals (§15).

    Tiers are a documented heuristic (thresholds below), used only to keep
    equivalent tasks comparable — never to claim speed across tiers.
    """
    lines_total = lines_added + lines_removed
    domain_count = len(set(domains))
    if domain_count >= 3 or files_changed >= 10 or lines_total >= 500:
        tier = "LARGE"
    elif domain_count >= 2 or files_changed >= 4 or lines_total >= 100 or multi_domain:
        tier = "MEDIUM"
    elif files_changed >= 1 or lines_total >= 1:
        tier = "SMALL"
    else:
        tier = "TRIVIAL"
    return {
        "tier": tier,
        "tier_heuristic": "LARGE: >=3 domains|>=10 files|>=500 lines; "
        "MEDIUM: >=2 domains|>=4 files|>=100 lines|multi; "
        "SMALL: any change; TRIVIAL: none",
        "files_changed": files_changed,
        "domains_touched": sorted(set(domains)),
        "lines_total": lines_total,
        "tests_affected": tests_affected,
        "public_surface": public_surface,
        "multi_domain": multi_domain,
    }
