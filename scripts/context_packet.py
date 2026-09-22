"""Edit-ready context packets (Phase 4).

Deterministic consumer (no new authority, no repository search, no codegen):
  patch_surface.resolve() (exact targets/impact/tests/safety/plan)
+ warm index (fresh lines/callers/deps + fingerprint for cache + freshness)
+ file reads bounded to target/test/caller files only.

Levels (cumulative): L0 routing, L1 target, L2 impact, L3 safety/execution.
Auto-escalation from the patch-surface validation scope: internal -> L1,
contract -> L1+L2, ownership/full -> L1+L2+L3.

Usage:
    python scripts/context_packet.py "add validation to broker registry"
    python scripts/context_packet.py "add validation to broker registry" --json
    python scripts/context_packet.py --symbol BrokerRegistry --level L1 --context-lines 6
    python scripts/context_packet.py --task "..." --max-bytes 20000 --no-cache
"""

from __future__ import annotations

import argparse
import ast
import contextlib
import hashlib
import json
import os
import sys
import time
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

import patch_surface  # noqa: E402
import repo_graph  # noqa: E402
import repo_index  # noqa: E402

ROOT = SCRIPTS_DIR.parent
PACKET_CACHE_DIR = repo_index.INDEX_DIR / "packets"

PACKET_SCHEMA = "context-packet/v1"
LEVELS = ("L0", "L1", "L2", "L3")
DEFAULT_CONTEXT_LINES = 6
MAX_BODY_LINES = 120
CALLER_SNIPPET_CAP = 5
CALLER_REF_CAP = 25
FALLBACK_BODY_CAP = 10
TEST_FILE_CAP = 3
TEST_SYMBOL_CAP = 3
PACKET_CACHE_KEEP = 100

# Validation scope -> packet levels (cumulative).
SCOPE_LEVELS = {
    "internal": ["L1"],
    "contract": ["L1", "L2"],
    "ownership": ["L1", "L2", "L3"],
    "full": ["L1", "L2", "L3"],
}


class PacketError(Exception):
    """Packet failure — caller maps to STALE/UNKNOWN, never to partial output."""


def symbol_extent(root: Path, relpath: str, line: int, kind: str) -> tuple[int, int]:
    """(start, end) 1-based extent of the symbol body. Deterministic, bounded.

    Python uses the AST (exact); Rust/Slint brace-match; anything else falls
    back to the definition line so callers can apply the context window.
    """
    try:
        text = (root / relpath).read_text(encoding="utf-8")
    except OSError as exc:
        raise PacketError(f"cannot read target file: {relpath}") from exc
    lines = text.splitlines()
    if relpath.endswith(".py"):
        try:
            tree = ast.parse(text)
        except SyntaxError:
            return (line, line)
        for node in tree.body:
            if (
                isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                and node.lineno == line
            ):
                return (line, node.end_lineno or line)
        return (line, line)
    if relpath.endswith((".rs", ".slint")) and kind != "mod":
        depth = 0
        opened = False
        for index in range(line - 1, min(len(lines), line + 400)):
            for char in lines[index]:
                if char == "{":
                    depth += 1
                    opened = True
                elif char == "}":
                    depth -= 1
            if opened and depth <= 0:
                return (line, index + 1)
        return (line, line)
    return (line, line)


def read_snippet(
    root: Path, relpath: str, start: int, end: int, *, before: int = 0, after: int = 0
) -> dict:
    """Bounded numbered snippet {start, end, text}. Never dumps whole files."""
    try:
        lines = (root / relpath).read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise PacketError(f"cannot read target file: {relpath}") from exc
    total = len(lines)
    lo = max(1, start - before)
    hi = min(total, end + after)
    return {"start": lo, "end": hi, "text": "\n".join(lines[lo - 1 : hi])}


def _target_manifest_sha(relpath: str) -> str | None:
    manifest = repo_index._read_json(repo_index.MANIFEST_FILE)
    if not isinstance(manifest, dict):
        return None
    entry = manifest.get(relpath)
    return entry.get("sha") if isinstance(entry, dict) else None


def read_snippet_checked(
    root: Path, relpath: str, start: int, end: int, *, before: int = 0, after: int = 0
) -> dict:
    """Snippet read guarded by the warm-index manifest (race-safe).

    The patch surface already resolved against a fresh index; if the file
    changed underneath us, refresh once and retry, else STALE (never stale
    snippets or line numbers).
    """
    expected = _target_manifest_sha(relpath)
    path = root / relpath
    if not path.is_file():
        raise PacketError(f"target deleted: {relpath}")
    try:
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise PacketError(f"cannot read target file: {relpath}") from exc
    if expected is not None and actual != expected:
        repo_index.refresh()
        expected = _target_manifest_sha(relpath)
        try:
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError as exc:
            raise PacketError(f"cannot read target file: {relpath}") from exc
        if expected is not None and actual != expected:
            raise PacketError(f"target changed during packet build: {relpath}")
    return read_snippet(root, relpath, start, end, before=before, after=after)


def _body_snippet(root: Path, target: dict, symbol: dict, context_lines: int) -> dict:
    start, end = symbol_extent(root, target["file"], symbol["line"], symbol["kind"])
    total = end - start + 1
    truncated = total > MAX_BODY_LINES
    if truncated:
        end = start + MAX_BODY_LINES - 1
    snippet = read_snippet_checked(
        root, target["file"], start, end, before=context_lines, after=context_lines
    )
    try:
        declaration = (
            (root / target["file"]).read_text(encoding="utf-8").splitlines()[symbol["line"] - 1]
        )
    except (OSError, IndexError):
        declaration = ""
    return {
        "start": start,
        "end": symbol_extent(root, target["file"], symbol["line"], symbol["kind"])[1],
        "total_lines": total,
        "declaration": declaration.strip(),
        "snippet": snippet,
        "truncated": truncated or None,
    }


def _caller_entries(
    payload: dict, root: Path, surface: dict, context_lines: int, with_snippets: bool
) -> tuple[list[dict], dict]:
    """Caller refs (+bounded snippets for material callers).

    Returns (entries, caps) with snippet cap and ref cap counts; ranked by
    materiality (test > same-file > same-module > cross-module).
    """
    by_qual = payload.get("maps", {}).get("by_qual", {})
    if not by_qual:
        by_qual = {s["qualified"]: s for s in payload["graph"].get("symbols", [])}
    target_files = {t["file"] for t in surface.get("targets", [])}
    target_modules = {t["module"] for t in surface.get("targets", [])}
    quals = surface.get("impact", {}).get("callers", [])
    ranked: list[tuple[tuple[int, str], dict]] = []
    for qual in quals:
        symbol = by_qual.get(qual)
        if symbol is None:
            continue
        is_test = repo_graph.is_test_file(symbol.get("file", ""))
        priority = (
            0
            if is_test
            else 1
            if symbol["file"] in target_files
            else 2
            if symbol["module"] in target_modules
            else 3
        )
        ranked.append(((priority, qual), symbol))
    ranked.sort(key=lambda item: item[0])
    entries: list[dict] = []
    for index, ((priority, qual), symbol) in enumerate(ranked):
        why = (
            "test caller"
            if priority == 0
            else "same-file caller"
            if symbol["file"] in target_files
            else "same-module caller"
            if symbol["module"] in target_modules
            else "cross-module caller"
        )
        entry: dict = {
            "qualified": qual,
            "file": symbol["file"],
            "line": symbol["line"],
            "why": why,
        }
        if with_snippets and index < CALLER_SNIPPET_CAP:
            start, end = symbol_extent(root, symbol["file"], symbol["line"], symbol["kind"])
            entry["snippet"] = read_snippet_checked(
                root,
                symbol["file"],
                start,
                min(end, start + MAX_BODY_LINES - 1),
                before=context_lines,
                after=0,
            )
            if end - start + 1 > MAX_BODY_LINES:
                entry["truncated"] = True
        elif with_snippets:
            entry["snippet_omitted"] = "cap"
        entries.append(entry)
    callers_omitted = max(0, len(entries) - CALLER_REF_CAP)
    if callers_omitted:
        entries = entries[:CALLER_REF_CAP]
    caps = {
        "snippets_capped": bool(with_snippets and len(ranked) > CALLER_SNIPPET_CAP),
        "refs_omitted": callers_omitted,
        "refs_total": len(ranked),
    }
    return entries, caps


def _test_entries(
    payload: dict, root: Path, surface: dict, context_lines: int, with_snippets: bool
) -> list[dict]:
    """Direct test symbols (call edges into targets) + module refs + missing flag."""
    graph = payload["graph"]
    target_quals = {
        s["qualified"] for t in surface.get("targets", []) for s in t.get("symbols", [])
    }
    direct_files = surface.get("tests", {}).get("direct", [])
    module_files = surface.get("tests", {}).get("module", [])
    by_path: dict[str, list[dict]] = {}
    for path in sorted(set(direct_files) | set(module_files)):
        file_symbols = sorted(
            (s for s in graph.get("symbols", []) if s["file"] == path),
            key=lambda s: (s["line"], s["qualified"]),
        )
        by_path[path] = [s for s in file_symbols if set(s.get("calls", [])) & target_quals]
    # Direct files first, then module files ranked by hit count (deterministic).
    ranked = sorted(set(direct_files)) + sorted(
        (p for p in set(module_files) - set(direct_files)),
        key=lambda p: (-len(by_path.get(p, [])), p),
    )
    entries: list[dict] = []
    for path in ranked[:TEST_FILE_CAP]:
        hitting = by_path.get(path, [])
        entry: dict = {"path": path, "relevance": "direct" if path in direct_files else "module"}
        if with_snippets and hitting:
            entry["symbols"] = []
            for symbol in hitting[:TEST_SYMBOL_CAP]:
                start, end = symbol_extent(root, path, symbol["line"], symbol["kind"])
                called = sorted(set(symbol.get("calls", [])) & target_quals)
                entry["symbols"].append(
                    {
                        "qualified": symbol["qualified"],
                        "line": symbol["line"],
                        "why": f"calls {', '.join(called)}",
                        "snippet": read_snippet_checked(
                            root,
                            path,
                            start,
                            min(end, start + MAX_BODY_LINES - 1),
                            before=0,
                            after=context_lines,
                        ),
                    }
                )
            if len(hitting) > TEST_SYMBOL_CAP:
                entry["symbols_truncated"] = True
        else:
            entry["symbols"] = [
                {"qualified": s["qualified"], "line": s["line"], "why": entry["relevance"]}
                for s in hitting
            ]
        entries.append(entry)
    for path in ranked[TEST_FILE_CAP:]:
        entries.append({"path": path, "relevance": "omitted by cap", "symbols": []})
    if not entries:
        note = surface.get("tests", {}).get("route", {}).get("note") or surface.get(
            "tests", {}
        ).get("route", {}).get("command")
        entries.append({"path": "", "relevance": "missing", "note": note or "no tests resolved"})
    return entries


def _contract_context(surface: dict, declared_by_module: dict[str, list[str]]) -> dict:
    route = surface.get("route", {}) or {}
    impact = surface.get("impact", {}) or {}
    allowed: dict[str, list[str]] = {}
    for mid in surface.get("impact", {}).get("target_modules", []) or []:
        allowed[mid] = sorted(declared_by_module.get(mid, []))
    boundary = str(route.get("language_boundary", "") or "")
    return {
        "interfaces": sorted(impact.get("affected_interfaces", []) or []),
        "ref": route.get("contract", "") or "",
        "missing": not bool(route.get("contract", "")),
        "allowed_directions": allowed,
        "forbidden_directions": sorted(route.get("forbidden", []) or []),
        "invariants": [boundary] if boundary else [],
    }


def _impact_context(surface: dict, with_details: bool) -> dict:
    impact = surface.get("impact", {}) or {}
    targets = surface.get("targets", []) or []
    incomplete = sorted(
        {
            s["qualified"]
            for t in targets
            for s in t.get("symbols", [])
            if s.get("caller_count", 0) == 0 and t["file"].endswith((".rs", ".slint"))
        }
    )
    base: dict = {
        "affected_modules": sorted(impact.get("affected_modules", []) or []),
        "dependencies": impact.get("dependencies", {}) or {},
        "dependents": impact.get("dependents_actual", {}) or impact.get("dependents", {}) or {},
    }
    if with_details:
        base["caller_files"] = sorted(impact.get("caller_files", []) or [])
        base["caller_modules"] = sorted(impact.get("caller_modules", []) or [])
    if incomplete:
        base["incomplete"] = True
        base["incomplete_reason"] = (
            "Rust/Slint caller edges are not tracked; "
            f"module dependents listed instead ({len(incomplete)} symbols)"
        )
        base["incomplete_symbols"] = incomplete
    return base


def _plan_context(surface: dict, tests: list[dict]) -> dict:
    validation = surface.get("validation", {}) or {}
    test_paths = [t["path"] for t in tests if t.get("path")]
    return {
        "read": sorted(
            f"{t['file']}:{s['qualified']}@{s['line']}"
            for t in surface.get("targets", []) or []
            for s in t.get("symbols", [])
        ),
        "understand": sorted(
            {
                surface.get("route", {}).get("contract", ""),
                surface.get("route", {}).get("language_boundary", ""),
            }
            - {""}
        ),
        "modify": sorted({t["file"] for t in surface.get("targets", []) or []}),
        "test": test_paths,
        "validate": list(validation.get("commands", []) or []),
        "forbidden": sorted((surface.get("route", {}) or {}).get("forbidden", []) or []),
    }


def _levels_for_scope(scope: str) -> list[str]:
    return list(SCOPE_LEVELS.get(scope, ["L1"]))


def _budget_of(packet: dict) -> dict:
    text = json.dumps(packet, sort_keys=True)

    def _span(item: dict) -> int:
        body = item.get("body", {})
        snippet = body.get("snippet", {}) if isinstance(body, dict) else {}
        if not snippet:
            snippet = item.get("snippet", {})
        if not isinstance(snippet, dict) or not snippet.get("text"):
            return 0
        return snippet.get("end", 0) - snippet.get("start", 0) + 1

    content_lines = sum(_span(s) for t in packet.get("targets", []) for s in t.get("symbols", []))
    content_lines += sum(_span(c) for c in packet.get("callers", []))
    content_lines += sum(_span(s) for t in packet.get("tests", []) for s in t.get("symbols", []))
    return {
        "bytes": len(text.encode("utf-8")),
        "chars": len(text),
        "lines": content_lines + len(packet.get("targets", [])),
        "files": len({t["file"] for t in packet.get("targets", [])}),
        "symbols": sum(len(t.get("symbols", [])) for t in packet.get("targets", [])),
        "snippets": sum(
            1 for t in packet.get("targets", []) for s in t.get("symbols", []) if "body" in s
        )
        + sum(1 for c in packet.get("callers", []) if "snippet" in c)
        + sum(1 for t in packet.get("tests", []) for s in t.get("symbols", []) if "snippet" in s),
    }


def _strip_for_budget(packet: dict, priority_kept: int) -> list[str]:
    """Drop lowest-priority sections first (7=secondary … 1=target symbol kept).

    Priority kept (never dropped below): target refs+lines > local context >
    direct tests > contract > critical callers > impact > secondary context.
    Returns names of stripped sections.
    """
    stripped: list[str] = []
    if priority_kept <= 6 and packet.get("callers"):
        for caller in packet["callers"]:
            if "snippet" in caller:
                del caller["snippet"]
                caller["snippet_omitted"] = "budget"
        stripped.append("caller-snippets")
    if priority_kept <= 5 and packet.get("impact"):
        packet["impact"] = {
            "affected_modules": packet["impact"].get("affected_modules", []),
            "incomplete": packet["impact"].get("incomplete", False),
        }
        stripped.append("impact-details")
    if priority_kept <= 4 and packet.get("callers"):
        packet["callers"] = [
            {
                "qualified": c["qualified"],
                "file": c["file"],
                "line": c["line"],
                "why": c.get("why", ""),
            }
            for c in packet["callers"]
        ]
        stripped.append("caller-refs-kept")
    if priority_kept <= 3 and packet.get("contract"):
        packet["contract"] = {
            "ref": packet["contract"].get("ref", ""),
            "missing": packet["contract"].get("missing", False),
        }
        stripped.append("contract-details")
    if priority_kept <= 2 and packet.get("tests"):
        for test in packet["tests"]:
            test.pop("symbols", None)
        stripped.append("test-snippets")
    return stripped


def _apply_budget(packet: dict, max_bytes: int | None) -> dict:
    budget = _budget_of(packet)
    budget["max_bytes"] = max_bytes
    budget["truncated_sections"] = []
    budget["exceeded"] = False
    if max_bytes is None or budget["bytes"] <= max_bytes:
        packet["budget"] = budget
        return packet
    for keep in (6, 5, 4, 3, 2):
        stripped = _strip_for_budget(packet, keep)
        budget = _budget_of(packet)
        budget["max_bytes"] = max_bytes
        budget["truncated_sections"] = stripped
        if budget["bytes"] <= max_bytes:
            budget["exceeded"] = False
            packet["budget"] = budget
            return packet
    budget["exceeded"] = True
    packet["budget"] = budget
    return packet


def _cache_key(
    task: str,
    level: str,
    context_lines: int,
    max_bytes: int | None,
    routes_sha: str,
    index_fingerprint: str | None,
    *,
    file: str | None = None,
    symbol: str | None = None,
    module: str | None = None,
    route_key: str | None = None,
    domain: str | None = None,
    modify: tuple[str, ...] = (),
) -> str:
    digest = hashlib.sha256()
    digest.update(" ".join(task.lower().split()).encode())
    digest.update(f"{level}/{context_lines}/{max_bytes}/{PACKET_SCHEMA}".encode())
    digest.update(
        f"{file or ''}/{symbol or ''}/{module or ''}/{route_key or ''}/{domain or ''}".encode()
    )
    digest.update(",".join(sorted(modify)).encode())
    digest.update(routes_sha.encode())
    digest.update((index_fingerprint or "none").encode())
    return digest.hexdigest()[:32]


def _cache_paths() -> None:
    PACKET_CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _hit_still_valid(cached: dict) -> bool:
    """Stat-verify every packet-referenced file against the index manifest.

    The cache key binds the index fingerprint, but the fingerprint is only
    as fresh as state.json. A tree change after a stale state leaves the
    key matching while sources moved: any size/mtime mismatch (or missing
    manifest entry) voids the hit. Graph-structure shifts outside packet
    files are caught downstream by manifest hashes + symbol re-queries.
    """
    manifest = repo_index._read_json(repo_index.MANIFEST_FILE)
    if not isinstance(manifest, dict):
        return False
    try:
        files: set[str] = set()
        for target in cached.get("targets", []) or []:
            if target.get("file"):
                files.add(target["file"])
        for caller in cached.get("callers", []) or []:
            if caller.get("file"):
                files.add(caller["file"])
        for test in cached.get("tests", []) or []:
            if test.get("path"):
                files.add(test["path"])
        for relpath in files:
            entry = manifest.get(relpath)
            if not isinstance(entry, dict):
                return False
            stat = repo_index._stat_entry(repo_index.ROOT / relpath)
            if stat is None:
                return False
            if entry.get("size") != stat[0] or entry.get("mtime_ns") != stat[1]:
                return False
    except (OSError, ValueError):
        return False
    return True


def _cache_load(key: str) -> dict | None:
    path = PACKET_CACHE_DIR / f"{key}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _cache_store(key: str, packet: dict) -> None:
    _cache_paths()
    path = PACKET_CACHE_DIR / f"{key}.json"
    tmp = path.with_name(f"{key}.tmp-{os.getpid()}")
    tmp.write_text(json.dumps(packet, sort_keys=True), encoding="utf-8", newline="\n")
    os.replace(tmp, path)
    try:
        files = sorted(
            PACKET_CACHE_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True
        )
        for stale in files[PACKET_CACHE_KEEP:]:
            if not stale.name.startswith("*.tmp"):
                stale.unlink(missing_ok=True)
    except OSError:
        pass


def _index_fingerprint() -> str | None:
    state = repo_index._read_json(repo_index.STATE_FILE)
    if not isinstance(state, dict):
        return None
    return state.get("fingerprint")


def _routes_sha() -> str:
    try:
        return hashlib.sha256((ROOT / "90_brain" / "task_routes.json").read_bytes()).hexdigest()
    except OSError:
        return "missing"


def build_packet(
    task: str = "",
    *,
    level: str = "auto",
    context_lines: int = DEFAULT_CONTEXT_LINES,
    max_bytes: int | None = None,
    use_cache: bool = True,
    file: str | None = None,
    symbol: str | None = None,
    module: str | None = None,
    route_key: str | None = None,
    domain: str | None = None,
    modify: list[str] | None = None,
) -> tuple[dict, str]:
    """TASK -> patch surface -> leveled packet. Returns (packet, cache_status)."""
    if level not in ("auto", *LEVELS):
        raise PacketError(f"unknown level {level}; use auto or one of {LEVELS}")
    # Cache first: the key binds task+config+routes+index-fingerprint, and the
    # fingerprint covers every source byte, so a hit cannot be stale. The
    # requested level (not resolved levels) keys the entry; resolved levels
    # derive deterministically from fingerprinted state.
    if use_cache:
        hit, key, fingerprint = _try_hit(
            task,
            level,
            context_lines,
            max_bytes,
            file,
            symbol,
            module,
            route_key,
            domain,
            modify,
        )
        if hit is not None:
            return hit, "hit"
    surface = patch_surface.resolve(
        task,
        file=file,
        symbol=symbol,
        module=module,
        route_key=route_key,
        domain=domain,
        modify=modify or [],
    )
    if surface["status"] != "RESOLVED":
        return _status_packet(surface, level, task), "miss"
    scope = surface.get("validation", {}).get("scope", "internal")
    levels = [level] if level != "auto" else _levels_for_scope(scope)

    # Re-read AFTER resolve: resolve refreshes the index when the tree moved,
    # so a pre-resolve key/fingerprint would attest new content with old
    # state (stale-keyed cache pollution). Recompute, re-check (a peer may
    # have stored it meanwhile), then assemble.
    if use_cache:
        hit, key, fingerprint = _try_hit(
            task,
            level,
            context_lines,
            max_bytes,
            file,
            symbol,
            module,
            route_key,
            domain,
            modify,
        )
        if hit is not None:
            return hit, "hit"
    else:
        fingerprint = _index_fingerprint()
        key = _cache_key(
            task,
            level,
            context_lines,
            max_bytes,
            _routes_sha(),
            fingerprint,
            file=file,
            symbol=symbol,
            module=module,
            route_key=route_key,
            domain=domain,
            modify=tuple(modify or []),
        )

    packet = _assemble(surface, task, levels, context_lines, max_bytes, key, fingerprint)
    if use_cache:
        _cache_store(key, packet)
    return packet, "miss"


def _try_hit(
    task: str,
    level: str,
    context_lines: int,
    max_bytes: int | None,
    file: str | None,
    symbol: str | None,
    module: str | None,
    route_key: str | None,
    domain: str | None,
    modify: list[str] | None,
) -> tuple[dict | None, str, str | None]:
    """Cache probe returning (packet|None, key, fingerprint)."""
    fingerprint = _index_fingerprint()
    key = _cache_key(
        task,
        level,
        context_lines,
        max_bytes,
        _routes_sha(),
        fingerprint,
        file=file,
        symbol=symbol,
        module=module,
        route_key=route_key,
        domain=domain,
        modify=tuple(modify or []),
    )
    cached = _cache_load(key)
    if (
        cached is not None
        and cached.get("cache", {}).get("key") == key
        and _hit_still_valid(cached)
    ):
        cached["cache"] = {"status": "hit", "key": key}
        return cached, key, fingerprint
    return None, key, fingerprint


def _assemble(
    surface: dict,
    task: str,
    levels: list[str],
    context_lines: int,
    max_bytes: int | None,
    key: str,
    fingerprint: str | None,
) -> dict:
    try:
        payload = repo_index.ensure_fresh()
    except (
        repo_index.IndexError,
        repo_index.IndexLockedError,
        repo_graph.GraphError,
        OSError,
        ValueError,
    ) as exc:
        return _stale_packet(task, f"index refresh failed during assembly: {exc}")
    graph = payload["graph"]
    with_snippets = True
    # Route/intent tasks center on route-listed symbols; other file symbols
    # stay navigation refs (minimal-context rule). Explicit file/symbol/module
    # intents keep full bodies. A route with no symbols map gives no
    # prioritization signal, so the most-connected symbols keep bodies
    # (bounded fallback cap) and the rest stay refs.
    kind = surface.get("resolution", {}).get("kind")
    any_listed = any(
        s.get("route_listed", False)
        for t in surface.get("targets", []) or []
        for s in t.get("symbols", []) or []
    )
    full_bodies = kind not in ("route", "intent")
    fallback_keep: set[str] = set()
    if not full_bodies and not any_listed:
        ranked = sorted(
            (s for t in surface.get("targets", []) or [] for s in t.get("symbols", []) or []),
            key=lambda s: (
                -len(s.get("callers", [])),
                0 if s.get("public") else 1,
                s.get("line", 0),
                s["qualified"],
            ),
        )
        fallback_keep = {s["qualified"] for s in ranked[:FALLBACK_BODY_CAP]}
    targets: list[dict] = []
    for target in surface.get("targets", []) or []:
        entry = {
            "file": target["file"],
            "module": target["module"],
            "owner": target.get("owner", ""),
            "language": target.get("language", ""),
            "symbols": [],
        }
        for symbol in target.get("symbols", []) or []:
            item = {
                "qualified": symbol["qualified"],
                "name": symbol["name"],
                "line": symbol["line"],
                "kind": symbol["kind"],
                "public": symbol["public"],
                "owner": symbol.get("owner", ""),
                "route_listed": symbol.get("route_listed", False),
            }
            if full_bodies or symbol.get("route_listed", False):
                item["callers"] = symbol.get("callers", [])
                item["tests"] = symbol.get("tests", [])
                item["body"] = _body_snippet(ROOT, target, symbol, context_lines)
            elif symbol["qualified"] in fallback_keep:
                item["callers"] = symbol.get("callers", [])
                item["tests"] = symbol.get("tests", [])
                item["body"] = _body_snippet(ROOT, target, symbol, context_lines)
                item["body_kept"] = "fallback-cap"
            else:
                # Navigation ref: pointer only (callers/tests stay in their
                # own sections; full evidence one index lookup away).
                item["body_omitted"] = "route-context"
            entry["symbols"].append(item)
        entry["symbols"].sort(key=lambda s: (s["line"], s["qualified"]))
        targets.append(entry)
    targets.sort(key=lambda t: t["file"])

    callers, caller_caps = _caller_entries(payload, ROOT, surface, context_lines, with_snippets)
    tests = _test_entries(payload, ROOT, surface, context_lines, with_snippets)
    declared_by_module = {
        m["id"]: list(m.get("depends_on_declared", [])) for m in graph.get("modules", [])
    }
    contract = _contract_context(surface, declared_by_module)
    impact = _impact_context(surface, with_details=True)
    plan = _plan_context(surface, tests)
    # READ mirrors the edit set: only symbols shipped with bodies.
    plan["read"] = sorted(
        f"{t['file']}:{s['qualified']}@{s['line']}"
        for t in targets
        for s in t.get("symbols", [])
        if "body" in s
    )
    route = surface.get("route", {}) or {}
    packet: dict = {
        "schema": PACKET_SCHEMA,
        "status": "RESOLVED",
        "task": " ".join(task.split()),
        "level_requested": "auto",
        "levels": levels,
        "route": {
            "key": route.get("key"),
            "domain": route.get("domain"),
            "owner": route.get("owner"),
            "language": route.get("language"),
            "contract": route.get("contract", ""),
        },
        "targets": targets,
        "callers": callers,
        "caller_caps": caller_caps,
        "tests": tests,
        "contract": contract,
        "impact": impact,
        "safety": {
            "must_change": surface.get("boundaries", {}).get("must_change", []),
            "may_change": surface.get("boundaries", {}).get("may_change", []),
            "must_not_change": surface.get("boundaries", {}).get("must_not_change", {}),
            "owner": surface.get("safety", {}).get("owner", []),
            "language": surface.get("safety", {}).get("language", []),
            "scope": surface.get("validation", {}).get("scope", ""),
            "blockers": [],
        },
        "plan": plan,
        "validation": surface.get("validation", {}),
        "missing": surface.get("missing", []),
        "evidence": {
            "patch_status": surface.get("status"),
            "resolution": surface.get("resolution", {}),
            "index_fingerprint": fingerprint,
            "graph_inputs_hash": graph.get("inputs_hash"),
            "routes_sha": _routes_sha(),
            "ownership_source": "90_brain/ownership_policy.json",
        },
        "cache": {"status": "miss", "key": key},
        "meta": {"scope": surface.get("validation", {}).get("scope", "")},
    }
    # Level shaping trims snippets/details only; plan + validation commands
    # stay complete at every level except L0 (compact references, not context).
    if "L2" not in levels:
        packet["callers"] = [
            {
                "qualified": c["qualified"],
                "file": c["file"],
                "line": c["line"],
                "why": c.get("why", ""),
            }
            for c in callers
        ]
        packet["tests"] = [
            {"path": t["path"], "relevance": t.get("relevance", ""), "note": t.get("note", "")}
            for t in tests
        ]
        packet["contract"] = {
            "ref": contract.get("ref", ""),
            "missing": contract.get("missing", False),
        }
        packet["impact"] = {
            "affected_modules": impact.get("affected_modules", []),
            "incomplete": impact.get("incomplete", False),
        }
    if "L3" not in levels:
        # Boundary refs are compact (paths/patterns only) and stay at every
        # level; only the expanded forbidden file list is L3 detail.
        must_not = surface.get("boundaries", {}).get("must_not_change", {}) or {}
        packet["safety"] = {
            "scope": surface.get("validation", {}).get("scope", ""),
            "blockers": [],
            "owner": surface.get("safety", {}).get("owner", []),
            "language": surface.get("safety", {}).get("language", []),
            "must_change": surface.get("boundaries", {}).get("must_change", []),
            "may_change": surface.get("boundaries", {}).get("may_change", []),
            "must_not_change": {"patterns": must_not.get("patterns", [])},
        }
    if levels == ["L0"]:
        packet["targets"] = [
            {"file": t["file"], "module": t["module"], "symbols": []} for t in targets
        ]
        packet["callers"] = []
        packet["tests"] = []
    return _apply_budget(packet, max_bytes)


def _status_packet(surface: dict, level: str, task: str) -> dict:
    """Preserve BLOCKED/UNKNOWN/NEEDS_CLARIFICATION (never editable)."""
    levels = [level] if level in LEVELS else ["L0"]
    return {
        "schema": PACKET_SCHEMA,
        "status": surface["status"],
        "task": " ".join(task.split()),
        "level_requested": level,
        "levels": levels,
        "route": surface.get("route", {}),
        "targets": [],
        "callers": [],
        "tests": [],
        "contract": {},
        "impact": {},
        "safety": surface.get("safety", {}),
        "plan": {},
        "validation": surface.get("validation", {}),
        "missing": surface.get("missing", []),
        "evidence": surface.get("evidence", {}),
        "unknown": surface.get("unknown", {}),
        "cache": {"status": "miss", "key": ""},
        "meta": {},
    }


def _stale_packet(task: str, reason: str) -> dict:
    return {
        "schema": PACKET_SCHEMA,
        "status": "STALE",
        "task": " ".join(task.split()),
        "level_requested": "auto",
        "levels": [],
        "route": {},
        "targets": [],
        "callers": [],
        "tests": [],
        "contract": {},
        "impact": {},
        "safety": {},
        "plan": {},
        "validation": {},
        "missing": [],
        "evidence": {},
        "unknown": {"reason": reason, "needed": ["refresh the warm index and retry"]},
        "cache": {"status": "miss", "key": ""},
        "meta": {},
    }


def render_text(packet: dict) -> str:
    """Compact scan: TASK/ROUTE/TARGET/CONTEXT/IMPACT/TEST/VALIDATE/FORBIDDEN."""
    lines = [f"STATUS: {packet.get('status', '?')} [{packet.get('schema', '?')}]"]
    lines.append(f"TASK: {packet.get('task', '')}  (levels: {','.join(packet.get('levels', []))})")
    if packet.get("status") != "RESOLVED":
        unknown = packet.get("unknown", {})
        lines.append(f"REASON: {unknown.get('reason', '?')}")
        for need in unknown.get("needed", []) or []:
            lines.append(f"  - {need}")
        for violation in packet.get("safety", {}).get("gate", {}).get("violations", []):
            lines.append(f"BLOCKED: {violation['target']} — {violation['reason']}")
        return "\n".join(lines)
    route = packet.get("route", {})
    lines.append(f"ROUTE: {route.get('key')} [{route.get('domain')}] ({route.get('language')})")
    targets = packet.get("targets", [])
    lines.append(f"TARGET ({len(targets)} files):")
    for target in targets[:6]:
        lines.append(f"  {target['file']} [{target['module']}]")
        for symbol in target.get("symbols", [])[:6]:
            body = symbol.get("body", {})
            mark = " [TRUNCATED]" if body.get("truncated") else ""
            lines.append(
                f"    L{symbol['line']}-{body.get('end', symbol['line'])}: "
                f"{symbol['qualified']}{mark}"
            )
    lines.append("CONTEXT:")
    for target in targets[:3]:
        for symbol in target.get("symbols", [])[:3]:
            snippet = symbol.get("body", {}).get("snippet", {})
            text = snippet.get("text", "")
            preview = "\n".join(
                f"      {n}: {t}" for n, t in _numbered(text, snippet.get("start", 0))[:12]
            )
            lines.append(f"    {symbol['qualified']}:\n{preview}")
    impact = packet.get("impact", {})
    lines.append(
        f"IMPACT: callers={len(packet.get('callers', []))} "
        f"modules={', '.join(impact.get('affected_modules', [])) or '—'}"
        + (" [IMPACT_INCOMPLETE]" if impact.get("incomplete") else "")
    )
    tests = packet.get("tests", [])
    shown = sorted(
        (t["path"] for t in tests if t.get("path")),
        key=lambda p: ("/__init__.py" in p, p),
    )[:4] or [t.get("note", "?") for t in tests[:1]]
    lines.append(f"TEST: {', '.join(shown) or '—'}")
    plan = packet.get("plan", {})
    validate_commands = plan.get("validate", []) or packet.get("validation", {}).get("commands", [])
    lines.append(f"VALIDATE [{packet.get('validation', {}).get('scope', '?')}]:")
    for command in validate_commands[:6]:
        lines.append(f"  {command}")
    safety = packet.get("safety", {})
    must_not = safety.get("must_not_change", {})
    lines.append(f"FORBIDDEN: {', '.join(must_not.get('patterns', [])[:5]) or '—'}")
    budget = packet.get("budget", {})
    lines.append(
        f"BUDGET: {budget.get('bytes', 0)} bytes / {budget.get('lines', 0)} lines / "
        f"{budget.get('files', 0)} files / {budget.get('snippets', 0)} snippets "
        f"[cache {packet.get('cache', {}).get('status', '?')}]"
    )
    return "\n".join(lines)


def _numbered(text: str, start: int) -> list[tuple[int, str]]:
    return [(start + i, line) for i, line in enumerate(text.splitlines())]


def audit_zero_discovery(packet: dict) -> dict:
    """Self-containment audit: can an AI act on this packet alone? (measured)."""
    targets = packet.get("targets", []) or []
    tests = packet.get("tests", []) or []
    plan = packet.get("plan", {}) or {}
    validation = packet.get("validation", {}) or {}
    safety = packet.get("safety", {}) or {}
    has_edit_region = any(
        s.get("body", {}).get("snippet", {}).get("text")
        for t in targets
        for s in t.get("symbols", [])
    )
    result = {
        "has_file": bool(targets and all(t.get("file") for t in targets)),
        "has_symbol": bool(any(t.get("symbols") for t in targets)),
        "has_edit_region": bool(has_edit_region),
        "has_test": bool([t for t in tests if t.get("path")] or plan.get("test")),
        "has_validation": bool(plan.get("validate") or validation.get("commands")),
        "has_forbidden": bool((safety.get("must_not_change", {}) or {}).get("patterns")),
    }
    result["complete"] = all(result.values())
    return result


def measure_reduction(task: str, **kwargs: str) -> dict:
    """A full target files vs B patch surface vs C packet (bytes/lines proxies)."""
    surface = patch_surface.resolve(task, **kwargs)  # type: ignore[arg-type]
    full_bytes = 0
    full_lines = 0
    files = sorted({t["file"] for t in surface.get("targets", []) or []})
    for relpath in files:
        try:
            text = (ROOT / relpath).read_text(encoding="utf-8")
        except OSError:
            continue
        full_bytes += len(text.encode("utf-8"))
        full_lines += text.count("\n") + 1
    surface_bytes = len(json.dumps(surface, sort_keys=True).encode("utf-8"))
    packet, _status = build_packet(task, use_cache=False, **kwargs)  # type: ignore[arg-type]
    packet_bytes = len(json.dumps(packet, sort_keys=True).encode("utf-8"))
    return {
        "task": task,
        "A_full_files": {"bytes": full_bytes, "lines": full_lines, "files": len(files)},
        "B_patch_surface": {"bytes": surface_bytes},
        "C_packet": {
            "bytes": packet_bytes,
            "lines": packet.get("budget", {}).get("lines", 0),
            "files": packet.get("budget", {}).get("files", 0),
            "snippets": packet.get("budget", {}).get("snippets", 0),
        },
    }


def benchmark() -> dict:
    """Cold/warm/cache-hit/miss/freshness/extraction latency breakdown."""
    task = "add validation to broker registry"
    out: dict = {}
    started = time.perf_counter()
    repo_index.refresh()
    out["refresh_ms"] = round((time.perf_counter() - started) * 1000, 1)
    started = time.perf_counter()
    repo_index.freshness()
    out["freshness_check_ms"] = round((time.perf_counter() - started) * 1000, 2)
    for key in list(PACKET_CACHE_DIR.glob("*.json")):
        with contextlib.suppress(OSError):
            key.unlink()
    started = time.perf_counter()
    build_packet(task, use_cache=False)
    out["cold_no_cache_ms"] = round((time.perf_counter() - started) * 1000, 1)
    started = time.perf_counter()
    build_packet(task, use_cache=True)
    out["cold_cache_miss_ms"] = round((time.perf_counter() - started) * 1000, 1)
    started = time.perf_counter()
    _packet, status = build_packet(task, use_cache=True)
    out["warm_cache_hit_ms"] = round((time.perf_counter() - started) * 1000, 2)
    out["warm_cache_status"] = status
    started = time.perf_counter()
    patch_surface.resolve(task)
    out["surface_only_ms"] = round((time.perf_counter() - started) * 1000, 1)
    out["extraction_overhead_ms"] = round(out["cold_no_cache_ms"] - out["surface_only_ms"], 1)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Edit-ready context packets")
    parser.add_argument("task", nargs="?", default="", help="task description")
    parser.add_argument("--task", dest="task_flag", default="", help="task description (flag form)")
    parser.add_argument("--file", default=None, help="explicit target file")
    parser.add_argument("--symbol", default=None, help="explicit target symbol")
    parser.add_argument("--module", default=None, help="explicit target module")
    parser.add_argument("--route", default=None, help="explicit route key")
    parser.add_argument("--domain", default=None, help="narrow to a route domain")
    parser.add_argument("--modify", action="append", default=[], help="planned edit target")
    parser.add_argument("--level", default="auto", help="auto or L0/L1/L2/L3")
    parser.add_argument("--context-lines", type=int, default=DEFAULT_CONTEXT_LINES)
    parser.add_argument("--max-bytes", type=int, default=None)
    parser.add_argument("--json", action="store_true", help="emit the packet as JSON")
    parser.add_argument("--no-cache", action="store_true", help="skip the packet cache")
    parser.add_argument("--stats", action="store_true", help="latency breakdown")
    args = parser.parse_args(argv)
    if args.stats:
        print(json.dumps(benchmark(), indent=2, sort_keys=True))
        return 0
    task_text = args.task_flag or args.task
    if not task_text and not any([args.file, args.symbol, args.module, args.route]):
        parser.print_help()
        return 2
    try:
        packet, _status = build_packet(
            task_text,
            level=args.level,
            context_lines=args.context_lines,
            max_bytes=args.max_bytes,
            use_cache=not args.no_cache,
            file=args.file,
            symbol=args.symbol,
            module=args.module,
            route_key=args.route,
            domain=args.domain,
            modify=args.modify,
        )
    except PacketError as exc:
        print(json.dumps({"status": "STALE", "reason": str(exc)}))
        return 1
    if args.json:
        print(json.dumps(packet, sort_keys=True))
    else:
        print(render_text(packet))
    return {"RESOLVED": 0, "BLOCKED": 1, "UNKNOWN": 2, "NEEDS_CLARIFICATION": 3, "STALE": 1}[
        packet["status"]
    ]


if __name__ == "__main__":
    raise SystemExit(main())
