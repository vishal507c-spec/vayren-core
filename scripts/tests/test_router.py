"""Router tests — determinism, priority, unknown handling (Phase 5 §10–§12).

Read-only against the real routing index (no repo writes); synthetic cases
use inline route lists. The live index must validate clean here — that is the
router self-validation contract (§13) exercised as a test.
"""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS_DIR))

from route import context_package, load_routes, match_route, unknown_package  # noqa: E402
from validate_routes import validate  # noqa: E402

ROOT = SCRIPTS_DIR.parent


def _inline_routes() -> list[dict]:
    return [
        {"key": "strategy-change", "aliases": ["strategy", "signal"]},
        {"key": "broker-ubl", "aliases": ["broker", "registry"]},
    ]


def test_exact_key_beats_alias() -> None:
    routes = _inline_routes()
    route, how = match_route("strategy-change", routes)
    assert route is not None and route["key"] == "strategy-change" and how == "exact-key"


def test_exact_alias_match() -> None:
    route, how = match_route("broker", _inline_routes())
    assert route is not None and route["key"] == "broker-ubl" and how == "exact-alias"


def test_substring_falls_back_to_pattern() -> None:
    route, how = match_route("add strategy parameter", _inline_routes())
    assert route is not None and route["key"] == "strategy-change"
    assert how == "alias-substring"


def test_most_specific_alias_wins_ties() -> None:
    routes = load_routes()
    route, how = match_route("market screen binding", routes)
    assert route is not None and route["key"] == "ui-screen"
    assert how == "alias-substring"


def test_deterministic_repeated_runs() -> None:
    routes = load_routes()
    first = match_route("order state table", routes)
    second = match_route("order state table", routes)
    assert first[0] is not None and second[0] is not None
    assert first[0]["key"] == second[0]["key"] == "order-lifecycle"
    assert first[1] == second[1]


def test_unknown_returns_controlled_discovery() -> None:
    route, how = match_route("blargh unknown thing", load_routes())
    assert route is None and how == "unknown"
    package = unknown_package("blargh unknown thing")
    assert package["matched_by"] == "unknown"
    assert len(package["next"]) == 4


def test_context_package_is_compact() -> None:
    routes = load_routes()
    route, how = match_route("broker", routes)
    assert route is not None
    package = context_package(route, how)
    assert package["owner"] == "09_broker/broker"
    assert package["language"] == "PYTHON"
    assert "09_broker/broker/registry.py" in package["primary_files"]
    assert package["forbidden"]
    assert package["validation"]


def test_live_index_validates_clean() -> None:
    routes = load_routes()
    assert len(routes) >= 16
    existing = {
        p.relative_to(ROOT).as_posix()
        for p in list(ROOT.rglob("*"))
        if p.is_file() and ".git/" not in p.parts
    }
    assert validate(routes, existing) == []


def test_no_duplicate_keys_or_aliases_in_live_index() -> None:
    routes = load_routes()
    keys = [r["key"] for r in routes]
    assert len(keys) == len(set(keys))
    aliases: list[str] = []
    for route in routes:
        aliases.extend(str(a).lower() for a in route.get("aliases", []))
    assert len(aliases) == len(set(aliases))
