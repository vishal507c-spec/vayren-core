"""Authority negative suite — every rule pinned valid→PASS / invalid→FAIL.

All cases run on inline source dicts (no repo writes, no git dependence).
Integration: the live tree must validate clean (that is the self-validation
contract exercised as a test). Wrong-domain-owner / forbidden-dependency /
stale-route cases live in test_language_governance.py + test_router.py and
scripts/validate_imports.py; the matrix below maps each rule to its test.
"""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS_DIR))

from validate_authority import (  # noqa: E402
    check_authorities,
    check_bridges,
    check_contracts,
    check_registries,
    check_route_contracts,
    check_selection_writers,
    check_ui_tokens,
)

BROKER_REG = "src/broker/registry.py"
STRAT_REG = "src/strategy/registry.py"
FACTORY = "src/data/provider/factory.py"


def _full_registries(extra: dict[str, str] | None = None) -> dict[str, str]:
    files = {
        BROKER_REG: "class BrokerRegistry:\n    pass\n_default_registry = None\n",
        STRAT_REG: "class StrategyRegistry:\n    pass\n",
        "src/core/ai/providers.py": "class AiProviderRegistry:\n    pass\n",
    }
    if extra:
        files.update(extra)
    return files


def _full_authorities(extra: dict[str, str] | None = None) -> dict[str, str]:
    files = {
        "src/broker/providers/zerodha/adapter.py": "class ZerodhaProvider:\n    pass\n",
        "src/broker/providers/fyers/adapter.py": "class FyersProvider:\n    pass\n",
        STRAT_REG: "class StrategyRegistry:\n    pass\n",
        BROKER_REG: ("class BrokerRegistry:\n    pass\ndef default_registry():\n    return None\n"),
        "src/data/settings.py": "class DownloadSettings:\n    pass\n",
        "src/broker/selection.py": "class BrokerSelection:\n    pass\n",
        "src/strategy/runtime.py": "class StrategyRuntime:\n    pass\n",
        FACTORY: "def build_provider(settings):\n    return None\n",
        "src/broker/providers/skeleton/__init__.py": ("def skeleton_record():\n    return None\n"),
        "src/strategy/language/compiler.py": ("def compile_strategy(code):\n    return None\n"),
        "src/app/services/broker_selection_service.py": (
            "class BrokerSelectionService:\n    pass\n"
        ),
    }
    if extra:
        files.update(extra)
    return files


def _rule_ids(violations: list) -> set[str]:
    return {v.rule for v in violations}


# ── single registry ────────────────────────────────────────────────────


def test_single_registry_passes() -> None:
    assert check_registries(_full_registries()) == []


def test_duplicate_registry_fails() -> None:
    files = _full_registries({"src/broker/other.py": "class BrokerRegistry:\n    pass\n"})
    violations = check_registries(files)
    assert "duplicate-registry" in _rule_ids(violations)
    assert any("src/broker/registry.py" in v.canonical for v in violations)


def test_shadow_registry_map_fails() -> None:
    files = _full_registries({"src/data/provider/shadow.py": "VENUE_REGISTRY = {}\n"})
    assert "shadow-registry-map" in _rule_ids(check_registries(files))


def test_shadow_registry_map_is_case_insensitive() -> None:
    files = _full_registries({"src/data/provider/shadow.py": "venue_registry = {}\n"})
    assert "shadow-registry-map" in _rule_ids(check_registries(files))


def test_unauthorized_register_writer_fails() -> None:
    files = _full_registries(
        {
            FACTORY: "registry.register(record)\n",
            "src/strategy/sneaky.py": "registry.register(record)\n",
        }
    )
    assert "unauthorized-registry-writer" in _rule_ids(check_registries(files))


def test_registry_constructed_outside_holder_fails() -> None:
    files = _full_registries({"src/app/x.py": "r = BrokerRegistry()\n"})
    assert "duplicate-registry-instance" in _rule_ids(check_registries(files))


# ── single authority ───────────────────────────────────────────────────


def test_single_authority_passes() -> None:
    assert check_authorities(_full_authorities()) == []


def test_duplicate_authority_fails_with_canonical() -> None:
    files = _full_authorities(
        {"src/data/other.py": "def build_provider(settings):\n    return None\n"}
    )
    violations = check_authorities(files)
    assert "duplicate-authority" in _rule_ids(violations)
    violation = next(v for v in violations if v.rule == "duplicate-authority")
    assert violation.canonical == FACTORY
    assert violation.symbol == "build_provider"


def test_migrated_table_authority_redefined_fails() -> None:
    files = _full_authorities({"src/execution/models/order_state.py": "TRANSITIONS = {}\n"})
    violations = check_authorities(files)
    assert "duplicate-table-authority" in _rule_ids(violations)
    violation = next(v for v in violations if v.rule == "duplicate-table-authority")
    assert violation.symbol == "TRANSITIONS"
    assert violation.canonical == "crates/vayren-core/src/order_state.rs"


def test_clean_tree_has_no_table_authority() -> None:
    assert check_authorities(_full_authorities()) == []


def test_function_local_constant_is_not_a_table_authority() -> None:
    files = _full_authorities(
        {
            "src/execution/models/order_state.py": (
                "def build():\n    TRANSITIONS = {}\n    return TRANSITIONS\n"
            )
        }
    )
    assert "duplicate-table-authority" not in _rule_ids(check_authorities(files))


# ── selection single writer ────────────────────────────────────────────


def test_canonical_selection_writer_passes() -> None:
    files = {
        "src/broker/selection_store.py": "s = BrokerSelection(n, e, t, r)\n",
        "src/app/services/broker_selection_service.py": "s = BrokerSelection(n, e, t, r)\n",
    }
    assert check_selection_writers(files) == []


def test_second_selection_writer_fails() -> None:
    files = {
        "src/broker/selection_store.py": "s = BrokerSelection(n, e, t, r)\n",
        "src/data/hijack.py": "s = BrokerSelection(n, e, t, r)\n",
    }
    assert "unauthorized-selection-writer" in _rule_ids(check_selection_writers(files))


def test_direct_provider_write_fails() -> None:
    files = {"src/data/hijack.py": "settings.provider = 'x'\n"}
    assert "direct-provider-write" in _rule_ids(check_selection_writers(files))


# ── public contracts ───────────────────────────────────────────────────


def test_valid_all_passes() -> None:
    files = {"src/broker/x.py": "X = 1\n__all__ = ['X']\n"}
    assert check_contracts(files) == []


def test_stale_export_fails() -> None:
    files = {"src/broker/x.py": "X = 1\n__all__ = ['X', 'Ghost']\n"}
    violations = check_contracts(files)
    assert "stale-export" in _rule_ids(violations)
    assert any(v.symbol == "Ghost" for v in violations)


def test_missing_all_on_contract_surface_fails() -> None:
    files = {"src/broker/__init__.py": '"""surface"""\n'}
    assert "missing-all" in _rule_ids(check_contracts(files))


# ── bridge purity ──────────────────────────────────────────────────────


def test_clean_bridge_passes() -> None:
    files = {
        "src/market/native_x.py": (
            "import ctypes\nfrom core.native.loader import load_vayren_core\n"
            "from market.models.bar import Bar\n_lib = load_vayren_core()\n"
        )
    }
    assert check_bridges(files) == []


def test_bridge_third_party_import_fails() -> None:
    files = {
        "src/market/native_x.py": (
            "import requests\nfrom core.native.loader import load_vayren_core\n"
        )
    }
    assert "bridge-import-violation" in _rule_ids(check_bridges(files))


def test_bridge_cross_domain_import_fails() -> None:
    files = {
        "src/market/native_x.py": "from strategy import Signal\n",
    }
    assert "bridge-import-violation" in _rule_ids(check_bridges(files))


def test_bridge_documented_exception_passes() -> None:
    files = {
        "src/backtest/native_validation.py": "from strategy import BacktestForm\n",
    }
    assert check_bridges(files) == []


def test_bridge_io_call_fails() -> None:
    files = {"src/risk/native_x.py": "fh = open('x')\n"}
    assert "bridge-io-violation" in _rule_ids(check_bridges(files))


def test_renamed_bridge_is_scanned_by_content() -> None:
    files = {
        "src/market/kernel_shim.py": (
            "import ctypes\nimport requests\nfrom core.native.loader import load_vayren_core\n"
        )
    }
    assert "bridge-import-violation" in _rule_ids(check_bridges(files))


def test_loader_package_is_scanned_by_content() -> None:
    files = {"src/core/native/loader.py": "import ctypes\nfrom strategy import Signal\n"}
    assert "bridge-import-violation" in _rule_ids(check_bridges(files))


def test_exec_call_in_bridge_fails() -> None:
    files = {"src/risk/native_x.py": "exec('x = 1')\n"}
    assert "bridge-io-violation" in _rule_ids(check_bridges(files))


def test_os_system_in_bridge_fails() -> None:
    files = {"src/risk/native_x.py": "import os\nos.system('ls')\n"}
    assert "bridge-io-violation" in _rule_ids(check_bridges(files))


def test_stdlib_system_call_in_bridge_passes() -> None:
    # Regression guard: only `os.system` is denied, `platform.system()` is not.
    files = {
        "src/core/native/loader.py": (
            "import ctypes\nimport platform\nif platform.system().lower() == 'win':\n    pass\n"
        )
    }
    assert check_bridges(files) == []


def test_non_ffi_module_is_not_treated_as_bridge() -> None:
    files = {"src/strategy/sma.py": "import ctypes\nfrom strategy.models import Signal\n"}
    assert check_bridges(files) == []


# ── UI tokens ──────────────────────────────────────────────────────────


def test_clean_ui_passes() -> None:
    slint = {
        "crates/vayren-shell/ui/palette.slint": "out property <color> bg: #070B10;\n",
        "crates/vayren-shell/ui/market.slint": "in property <color> ink: VayrenPalette.muted;\n",
    }
    assert check_ui_tokens(slint) == []


def test_hardcoded_token_outside_palette_fails() -> None:
    slint = {
        "crates/vayren-shell/ui/palette.slint": "out property <color> bg: #070B10;\n",
        "crates/vayren-shell/ui/rogue.slint": "Rectangle { color: #303030; }\n",
    }
    violations = check_ui_tokens(slint)
    assert "ui-token-violation" in _rule_ids(violations)


def test_duplicate_palette_token_fails() -> None:
    slint = {
        "crates/vayren-shell/ui/palette.slint": (
            "out property <color> bg: #070B10;\nout property <color> bg: #0B1017;\n"
        )
    }
    assert "duplicate-token" in _rule_ids(check_ui_tokens(slint))


# ── route contract drift ───────────────────────────────────────────────


def test_route_symbol_in_ancestor_all_passes() -> None:
    files = {
        "src/broker/registry.py": "class BrokerRegistry:\n    pass\n",
        "src/broker/__init__.py": (
            "from broker.registry import BrokerRegistry\n__all__ = ['BrokerRegistry']\n"
        ),
    }
    routes = [
        {
            "key": "r",
            "symbols": {"src/broker/registry.py": ["BrokerRegistry"]},
        }
    ]
    assert check_route_contracts(routes, files) == []


def test_route_symbol_missing_from_all_fails() -> None:
    files = {
        "src/broker/registry.py": "class BrokerRegistry:\n    pass\n",
        "src/broker/__init__.py": "__all__ = []\n",
    }
    routes = [
        {
            "key": "r",
            "symbols": {"src/broker/registry.py": ["BrokerRegistry"]},
        }
    ]
    assert "contract-drift" in _rule_ids(check_route_contracts(routes, files))


def test_route_language_matches_policy() -> None:
    from validate_authority import check_route_languages as check_languages  # noqa: E402

    policy = [
        {
            "directory_prefixes": ["src/broker/"],
            "excluded_subpaths": [],
            "required_language": "PYTHON",
        }
    ]
    routes = [
        {
            "key": "ok",
            "language": "PYTHON",
            "primary_files": ["src/broker/registry.py"],
        },
        {
            "key": "bad",
            "language": "RUST",
            "primary_files": ["src/broker/registry.py"],
        },
    ]
    violations = check_languages(routes, policy)
    assert len(violations) == 1 and violations[0].symbol == "bad"
