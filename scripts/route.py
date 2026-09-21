"""Task router — machine-readable task -> context package (Phase 5).

Deterministic: same task string -> same route while the tree is unchanged
(exact key > exact alias > alias substring > UNKNOWN). The router never
searches the repository; uncertain input returns UNKNOWN with controlled
discovery steps instead of a forced (possibly wrong) route.

Usage:
    python scripts/route.py --task "add strategy parameter"
    python scripts/route.py --task "order state" --json
    python scripts/route.py --list
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ROUTES_FILE = ROOT / "90_brain" / "task_routes.json"


def load_routes(path: Path = ROUTES_FILE) -> list[dict]:
    """Load route records (read-only). Missing/unreadable -> empty list."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    routes = data.get("routes", []) if isinstance(data, dict) else []
    return [r for r in routes if isinstance(r, dict)]


def match_route(task: str, routes: list[dict]) -> tuple[dict | None, str]:
    """Pure deterministic match. Returns (route|None, how).

    Priority: exact key (3) > exact alias (2) > alias substring (1); within
    one level the LONGEST matching alias wins (most specific intent); any
    remaining tie resolves in file order. No match -> (None, "unknown").
    """
    want = " ".join(task.lower().split())
    if not want:
        return None, "unknown"
    best: dict | None = None
    best_key: tuple[int, int, int] = (0, 0, 0)
    best_how = "unknown"
    for index, route in enumerate(routes):
        key = str(route.get("key", "")).lower()
        aliases = [str(a).lower() for a in route.get("aliases", []) if a]
        if want == key:
            score, how, width = 3, "exact-key", len(key)
        elif want in aliases:
            score, how = 2, "exact-alias"
            width = len(want)
        else:
            hits = [len(a) for a in aliases if a and (want in a or a in want)]
            if not hits:
                continue
            score, how, width = 1, "alias-substring", max(hits)
        candidate = (score, width, -index)
        if candidate > best_key:
            best, best_key, best_how = route, candidate, how
    return best, best_how


def context_package(route: dict, how: str) -> dict:
    """Compact task-context handoff (§17): fields scoped to the task only."""
    return {
        "task": route.get("key"),
        "matched_by": how,
        "domain": route.get("domain"),
        "owner": route.get("owner_module"),
        "language": route.get("language"),
        "language_boundary": route.get("language_boundary", ""),
        "primary_files": route.get("primary_files", []),
        "secondary_files": route.get("secondary_files", []),
        "depends_on": route.get("depends_on", []),
        "depended_on_by": route.get("depended_on_by", []),
        "contract": route.get("contract", ""),
        "symbols": route.get("symbols", {}),
        "tests": route.get("tests", {}),
        "validation": route.get("validation", []),
        "forbidden": route.get("forbidden", []),
        "change_surface": route.get("change_surface", {}),
    }


def unknown_package(task: str) -> dict:
    """Controlled discovery instead of a forced route (§12)."""
    return {
        "task": task,
        "matched_by": "unknown",
        "next": [
            "grep AI_ENTRY.md routing table for the domain keyword",
            "identify the owner module via 90_brain/ownership_policy.json",
            "confirm with scripts/validate_imports.py DOMAIN_DEPS",
            "add a route + aliases to 90_brain/task_routes.json",
        ],
    }


def render_text(package: dict) -> str:
    """One compact screen: facts an agent acts on, nothing else."""
    if package.get("matched_by") == "unknown":
        lines = [f"TASK: {package['task']} -> UNKNOWN (no forced route)", "NEXT:"]
        lines.extend(f"  - {step}" for step in package["next"])
        return "\n".join(lines)
    lines = [
        f"TASK: {package['task']}  (matched by {package['matched_by']})",
        f"OWNER: {package['owner']}  [{package['domain']}]",
        f"LANGUAGE: {package['language']}",
    ]
    if package.get("language_boundary"):
        lines.append(f"BOUNDARY: {package['language_boundary']}")
    lines.append("PRIMARY FILES:")
    lines.extend(f"  {f}" for f in package["primary_files"])
    if package.get("secondary_files"):
        lines.append("SECONDARY FILES:")
        lines.extend(f"  {f}" for f in package["secondary_files"])
    lines.append(f"DEPENDS ON: {', '.join(package['depends_on']) or '—'}")
    if package.get("depended_on_by"):
        lines.append(f"DEPENDED ON BY: {'; '.join(package['depended_on_by'])}")
    lines.append(f"CONTRACT: {package['contract']}")
    if package.get("symbols"):
        lines.append("SYMBOLS:")
        for mod, names in package["symbols"].items():
            lines.append(f"  {mod}: {', '.join(names)}")
    tests = package.get("tests", {})
    lines.append(f"TESTS: {tests.get('command', tests.get('note', 'see validation'))}")
    lines.append("VALIDATION:")
    lines.extend(f"  {c}" for c in package["validation"])
    lines.append("FORBIDDEN:")
    lines.extend(f"  {f}" for f in package["forbidden"])
    cs = package.get("change_surface", {})
    if cs:
        lines.append(f"CHANGE SURFACE: likely {', '.join(cs.get('likely_changed', []))}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Route a task to its context package")
    parser.add_argument("--task", default="", help="task description / domain keyword")
    parser.add_argument("--json", action="store_true", help="emit the package as JSON")
    parser.add_argument("--list", action="store_true", help="list route keys")
    args = parser.parse_args(argv)
    routes = load_routes()
    if args.list:
        for route in routes:
            print(route.get("key", "?"))
        return 0
    route, how = match_route(args.task, routes)
    package = context_package(route, how) if route else unknown_package(args.task)
    if args.json:
        print(json.dumps(package, indent=2))
    else:
        print(render_text(package))
    return 0 if route else 2


if __name__ == "__main__":
    raise SystemExit(main())
