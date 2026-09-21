"""Context-engine tests — deterministic routing, packets, safety, drift, cache.

Read-only against the real routing index (no repo writes); synthetic cases use
inline data or tmp files. Packet content for known tasks is pinned to the
current tree — a rename/move breaks these tests exactly like it breaks the
validators (stale references fail loudly, never silently).
"""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS_DIR))

from context_engine import (  # noqa: E402
    _cache_key,
    build_packet,
    change_surface,
    escalate,
    execution_plan,
    resolve_symbol,
    safety_check,
    scope_check,
    select_tests,
    self_check,
    symbol_snippet,
    validate_packet,
)
from route import load_routes  # noqa: E402

ROOT = SCRIPTS_DIR.parent


def _routes() -> list[dict]:
    routes = load_routes()
    assert routes, "routing index must load"
    return routes


# ── known task / alias / unknown ─────────────────────────────────────


def test_known_task_routes_to_correct_context() -> None:
    packet, _ = build_packet("add strategy parameter", _routes(), use_cache=False)
    assert packet["status"] == "OK"
    assert packet["route"] == "strategy-change"
    assert packet["owner"] == "05_strategy/strategy"
    assert packet["language"] == "PYTHON"
    assert "05_strategy/strategy/runtime.py" in packet["primary_files"]


def test_alias_resolves_to_same_context() -> None:
    first, _ = build_packet("broker", _routes(), use_cache=False)
    second, _ = build_packet("selection", _routes(), use_cache=False)
    assert first["route"] == second["route"] == "broker-ubl"


def test_unknown_task_uses_controlled_discovery() -> None:
    packet, _ = build_packet("blargh xyzzy", _routes(), use_cache=False)
    assert packet["status"] == "UNKNOWN"
    assert len(packet["next"]) == 4


def test_malformed_task_input() -> None:
    packet, _ = build_packet("", _routes(), use_cache=False)
    assert packet["status"] == "UNKNOWN"
    bad_level, _ = build_packet("broker", _routes(), level="L9", use_cache=False)
    assert bad_level["status"] == "CONTEXT FAIL" and bad_level["rule"] == "bad-level"
    bad_scope, _ = build_packet("broker", _routes(), scope="cosmic", use_cache=False)
    assert bad_scope["status"] == "CONTEXT FAIL" and bad_scope["rule"] == "bad-scope"


# ── symbol resolution ────────────────────────────────────────────────


def test_resolve_symbol_inline() -> None:
    assert resolve_symbol("Foo", "class Foo:\n    pass\n") == 1
    assert resolve_symbol("bar", "x = 1\ndef bar():\n    pass\n") == 2
    assert resolve_symbol("pub_fn", "pub fn pub_fn() {}\n") == 1
    assert resolve_symbol("Missing", "x = 1\n") is None


def test_resolve_symbol_live_tree() -> None:
    source = (ROOT / "09_broker/broker/registry.py").read_text(encoding="utf-8")
    line = resolve_symbol("BrokerRegistry", source)
    assert line == 68
    rs = (ROOT / "rust/vayren-shell/src/market.rs").read_text(encoding="utf-8")
    assert resolve_symbol("MarketBar", rs) == 24


def test_packet_symbols_carry_lines_and_callers() -> None:
    packet, _ = build_packet("broker", _routes(), level="L1", use_cache=False)
    symbols = packet["symbols"]
    assert symbols["BrokerRegistry"]["file"] == "09_broker/broker/registry.py"
    assert isinstance(symbols["BrokerRegistry"]["line"], int)
    assert isinstance(symbols["BrokerRegistry"]["callers"], list)


# ── dependencies / change surface ────────────────────────────────────


def test_dependency_resolution() -> None:
    packet, _ = build_packet("add strategy parameter", _routes(), level="L2", use_cache=False)
    assert "01_core/core" in packet["depends_on"]
    assert "03_market/market" in packet["depends_on"]


def test_change_surface_generation() -> None:
    routes = _routes()
    route = next(r for r in routes if r["key"] == "strategy-change")
    surface = change_surface(route)
    assert surface["likely_changed"]
    assert surface["affected_interfaces"]
    assert surface["required_tests"]


# ── validation escalation ────────────────────────────────────────────


def test_escalation_levels_are_deterministic() -> None:
    assert escalate("internal", False)[0] == "internal"
    assert escalate("contract", False)[0] == "contract"
    assert escalate("ownership", False)[0] == "ownership"
    assert escalate("full", False)[0] == "full"
    assert escalate("internal", True)[0] == "contract"
    assert escalate("full", True)[0] == "full"


def test_test_selection_reports_reason() -> None:
    routes = _routes()
    route = next(r for r in routes if r["key"] == "broker-ubl")
    local = select_tests(route, "internal")
    assert "pytest 09_broker/broker/tests" in local["targeted"]
    assert local["escalated"] is None
    full = select_tests(route, "full")
    assert "make check" in full["escalated"]


# ── forbidden targets / ownership / language ─────────────────────────


def test_forbidden_target_blocked() -> None:
    routes = _routes()
    route = next(r for r in routes if r["key"] == "strategy-change")
    blocked = safety_check(route, ["rust/vayren-core/src/metrics.rs"])
    assert len(blocked) == 1 and "forbidden" in blocked[0]["reason"]


def test_missing_target_blocked() -> None:
    routes = _routes()
    route = next(r for r in routes if r["key"] == "broker-ubl")
    blocked = safety_check(route, ["09_broker/broker/nope.py"])
    assert len(blocked) == 1 and "does not exist" in blocked[0]["reason"]


def test_language_mismatch_blocked() -> None:
    routes = _routes()
    route = next(r for r in routes if r["key"] == "strategy-change")
    blocked = safety_check(route, ["rust/vayren-core/src/lib.rs"])
    assert blocked, "Rust file on a PYTHON route must block"


def test_allowed_target_passes_safety() -> None:
    routes = _routes()
    route = next(r for r in routes if r["key"] == "broker-ubl")
    assert safety_check(route, ["09_broker/broker/registry.py"]) == []


# ── scope drift ──────────────────────────────────────────────────────


def test_scope_ok_when_planned_matches() -> None:
    result = scope_check(["a.py"], ["a.py"], ["rust/*"])
    assert result["action"] == "ok" and result["unexpected"] == []


def test_scope_escalates_on_unexpected_file() -> None:
    result = scope_check(["a.py"], ["a.py", "b.py"], ["rust/*"])
    assert result["action"] == "escalate" and result["unexpected"] == ["b.py"]


def test_scope_blocks_on_forbidden_file() -> None:
    result = scope_check(["a.py"], ["a.py", "rust/x.rs"], ["rust/*"])
    assert result["action"] == "block"


# ── execution plan ───────────────────────────────────────────────────


def test_execution_plan_from_packet() -> None:
    packet, _ = build_packet("broker", _routes(), level="L3", use_cache=False)
    plan = execution_plan(packet, ["09_broker/broker/registry.py"])
    assert plan["MODIFY"] == ["09_broker/broker/registry.py"]
    assert plan["READ"]
    assert plan["FORBIDDEN"]
    assert plan["VALIDATE"]


# ── cache ────────────────────────────────────────────────────────────


def test_cache_key_stable_and_sensitive() -> None:
    assert _cache_key("broker", "L1", "internal") == _cache_key("broker", "L1", "internal")
    assert _cache_key("broker", "L1", "internal") != _cache_key("broker", "L2", "internal")
    assert _cache_key("broker", "L1", "internal") != _cache_key("strategy", "L1", "internal")


def test_cache_hit_on_repeat() -> None:
    routes = _routes()
    _, first = build_packet("broker-ubl", routes, use_cache=True)
    _, second = build_packet("broker-ubl", routes, use_cache=True)
    assert second == "hit"
    assert first in ("hit", "miss")


# ── engine self-check ────────────────────────────────────────────────


def test_self_check_passes_on_live_index() -> None:
    assert self_check() == []


# ── snippets / boundary escalation / packet validation ─────────────────


def test_symbol_snippet_window() -> None:
    snippet = symbol_snippet("line 10\nline 11\nline 12", 2, 1)
    assert "2: line 11" in snippet
    assert symbol_snippet("x", None, 5) == ""
    assert symbol_snippet("x = 1\n", 1, 0) == ""


def test_snippet_in_packet_with_window() -> None:
    packet, _ = build_packet("broker", _routes(), use_cache=False)
    assert "snippet" not in packet["symbols"]["BrokerRegistry"]
    windowed, _ = build_packet("broker", _routes(), use_cache=False, window=2)
    snippet = windowed["symbols"]["BrokerRegistry"].get("snippet", "")
    assert "class BrokerRegistry" in snippet


def test_boundary_crossing_escalates() -> None:
    from context_engine import main as engine_main  # noqa: E402

    rc = engine_main(
        [
            "--task",
            "market data read",
            "--modify",
            "03_market/market/models/bar.py,rust/vayren-core/src/market.rs",
        ]
    )
    assert rc == 0  # multi-language modify set escalates, does not block


def test_validate_packet_detects_stale_content() -> None:
    packet, _ = build_packet("broker", _routes(), use_cache=False)
    assert validate_packet(packet) == []
    stale = dict(packet)
    stale["primary_files"] = ["09_broker/broker/gone.py"]
    assert any("gone" in problem for problem in validate_packet(stale))
    broken_symbols = dict(packet)
    broken_symbols["symbols"] = {"Ghost": {"file": "09_broker/broker/x.py", "line": None}}
    assert any("Ghost" in problem for problem in validate_packet(broken_symbols))
