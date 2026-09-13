"""Mechanical repair: rustc diagnostics → candidate-only patches.

Repairs are strictly mechanical (casts, literal suffixes, names). Anything
semantic — changed expectations, weakened asserts, golden edits — is
refused: the repair returns None and the pipeline aborts with evidence.
Golden files, oracles and production code are never touched here.
"""

from __future__ import annotations

import re

_MISMATCH = re.compile(r"expected `([^`]+)`, found `([^`]+)`")
_LINE = re.compile(r"-->\s*src/risk_kernel\.rs:(\d+):(\d+)")


def _apply_cast(source: str, lineno: int, expected: str) -> str | None:
    lines = source.splitlines()
    if not 1 <= lineno <= len(lines):
        return None
    line = lines[lineno - 1]
    suffix = {"f64": " as f64", "i64": " as i64", "u32": " as u32", "bool": ""}[expected]
    if not suffix:
        return None
    stripped = line.rstrip()
    if stripped.endswith("{") or stripped.endswith("}") or " as f64" in line or " as i64" in line:
        return None
    lines[lineno - 1] = stripped + suffix
    return "\n".join(lines) + "\n"


def _apply_float_literal(source: str, lineno: int, col: int) -> str | None:
    import re as _re

    lines = source.splitlines()
    if not 1 <= lineno <= len(lines):
        return None
    line = lines[lineno - 1]
    for match in _re.finditer(r"(?<![\w.])(\d+)(?![\w.])", line):
        if match.start() >= col - 1:
            fixed = line[: match.end()] + ".0" + line[match.end() :]
            lines[lineno - 1] = fixed
            return "\n".join(lines) + "\n"
    return None


def repair_candidate(source: str, log: str) -> tuple[str | None, str]:
    """Return (fixed_source, note) or (None, reason) when unrepairable."""
    if "mismatched types" in log or "expected `" in log:
        match_line = _LINE.search(log)
        match_types = _MISMATCH.search(log)
        if match_line and match_types:
            expected = match_types.group(1)
            if expected in ("f64", "i64", "u32"):
                fixed = _apply_cast(source, int(match_line.group(1)), expected)
                if fixed is not None:
                    return fixed, f"cast to {expected} at line {match_line.group(1)}"
        if "use a float literal" in log and match_line:
            fixed = _apply_float_literal(source, int(match_line.group(1)), int(match_line.group(2)))
            if fixed is not None:
                return fixed, f"float literal at line {match_line.group(1)}"
    if "cannot find value" in log:
        name = re.search(r"cannot find value `([^`]+)`", log)
        if name:
            return None, f"unresolved name {name.group(1)} — spec regeneration required"
    if "assertion failed" in log or "test result: FAILED" in log:
        return None, "semantic test failure — expectations immutable, aborting"
    if "panicked" in log:
        return None, "candidate panicked — aborting, no silent coercion"
    return None, "no applicable mechanical repair"


__all__ = ["repair_candidate"]
