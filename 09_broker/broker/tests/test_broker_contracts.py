"""M1–M3 contract tests: vocab, capabilities, faces, registry, selection.

Every test proves a design-doc rule (references are to
``90_brain/broker_layer_design.md``). No network, no SDKs, no secrets.
"""

from __future__ import annotations

import sys
from pathlib import Path

BROKER_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BROKER_DIR))

import pytest  # noqa: E402

from broker.capabilities import (  # noqa: E402
    DOMAIN_ITEMS,
    CapabilitySet,
    Caps,
    Domain,
    capability_set,
    market_data_set_from_legacy,
    trading_set_from_legacy,
)
from broker.faces import FactoryPlugin, StaticPlugin  # noqa: E402
from broker.registry import (  # noqa: E402
    BrokerRecord,
    BrokerRegistry,
    DuplicateBrokerError,
)
from broker.selection import (  # noqa: E402
    BrokerSelection,
    MemorySelectionStore,
    SelectionError,
    surface_resolution,
)
from broker.vocab import (  # noqa: E402
    BrokerError,
    BrokerNotRegisteredError,
    CredentialsNotReadyError,
    Environment,
    ErrorCode,
    UnsupportedCapabilityError,
    error_code_from_legacy,
)

TS = "2026-09-06T14:00:00+05:30"


# ── M1: vocabulary ────────────────────────────────────────────────────────


def test_error_code_translation_table_covers_both_layers() -> None:
    # Data layer's 7 codes (design §4.1: subset relation)
    assert error_code_from_legacy("AUTHENTICATION_FAILED") is ErrorCode.AUTHENTICATION_FAILED
    assert error_code_from_legacy("RATE_LIMITED") is ErrorCode.RATE_LIMITED
    assert error_code_from_legacy("INVALID_SYMBOL") is ErrorCode.INVALID_SYMBOL
    assert error_code_from_legacy("NETWORK_ERROR") is ErrorCode.NETWORK_ERROR
    assert error_code_from_legacy("PROVIDER_UNAVAILABLE") is ErrorCode.PROVIDER_UNAVAILABLE
    assert error_code_from_legacy("INVALID_REQUEST") is ErrorCode.INVALID_REQUEST
    assert error_code_from_legacy("UNKNOWN_PROVIDER_ERROR") is ErrorCode.UNKNOWN
    # Execution layer's codes
    assert error_code_from_legacy("DUPLICATE") is ErrorCode.DUPLICATE_ORDER
    assert error_code_from_legacy("NOT_CONNECTED") is ErrorCode.NOT_CONNECTED
    assert error_code_from_legacy("CREDENTIALS") is ErrorCode.CREDENTIALS_NOT_READY
    # Unknown codes never get invented meanings (design §12 test 1)
    assert error_code_from_legacy("SOMETHING_NEW") is ErrorCode.UNKNOWN


def test_typed_errors_carry_normalized_codes() -> None:
    assert BrokerError("x").code is ErrorCode.UNKNOWN
    assert BrokerNotRegisteredError("x").code is ErrorCode.NOT_REGISTERED
    assert UnsupportedCapabilityError("x").code is ErrorCode.CAPABILITY_UNSUPPORTED
    assert CredentialsNotReadyError("x").code is ErrorCode.CREDENTIALS_NOT_READY


def test_domain_and_environment_enums() -> None:
    assert [d.value for d in Domain] == ["historical_data", "market_data", "trading"]
    assert [e.value for e in Environment] == ["paper", "sandbox", "live"]


# ── M1: capability model ──────────────────────────────────────────────────


def test_capability_set_supports_missing_and_domains() -> None:
    trading = capability_set({Domain.TRADING: (Caps.ORDERS_MARKET, Caps.ACCOUNT_POSITIONS)})
    assert trading.supports(Caps.ORDERS_MARKET)
    assert not trading.supports(Caps.ORDERS_LIMIT)
    assert trading.supports_domain(Domain.TRADING)
    assert not trading.supports_domain(Domain.HISTORICAL_DATA)
    assert trading.missing((Caps.ORDERS_MARKET, Caps.ORDERS_CANCEL)) == (Caps.ORDERS_CANCEL,)


def test_capability_set_rejects_unknown_ids_and_stray_domains() -> None:
    with pytest.raises(ValueError):
        CapabilitySet(domains=(Domain.TRADING,), items=frozenset({"warp.drive"}))
    with pytest.raises(ValueError):
        CapabilitySet(domains=(Domain.TRADING,), items=frozenset())
    with pytest.raises(ValueError):
        capability_set({"galaxy.far": ()})  # type: ignore[dict-item]


def test_capability_set_union_no_phantom() -> None:
    a = capability_set({Domain.TRADING: (Caps.ORDERS_MARKET,)})
    b = capability_set({Domain.HISTORICAL_DATA: (Caps.HIST_CANDLES,)})
    merged = a.union(b)
    assert set(merged.domains) == {Domain.TRADING, Domain.HISTORICAL_DATA}
    assert merged.supports(Caps.ORDERS_MARKET) and merged.supports(Caps.HIST_CANDLES)
    assert len(merged.items) == 2  # no phantom capabilities (§6 rule 3)


def test_funds_capability_present_in_trading_domain() -> None:
    assert Caps.ACCOUNT_FUNDS in DOMAIN_ITEMS[Domain.TRADING]  # D8 resolution


def test_legacy_translation_maps() -> None:
    trading = trading_set_from_legacy(("orders.market", "stream.events"))
    assert trading.supports(Caps.ORDERS_MARKET)
    assert trading.supports(Caps.STREAM_FILLS)
    md = market_data_set_from_legacy(("candle-close", "heartbeat", "mystery"))
    assert md.supports(Caps.MD_CANDLE_STREAM) and md.supports(Caps.MD_HEARTBEAT)
    assert len(md.items) == 2  # unknown legacy strings are dropped, not invented


# ── M1: faces / plugins ───────────────────────────────────────────────────


class _FakeTrading:
    name = "fake"
    environment = Environment.PAPER

    def capabilities(self) -> CapabilitySet:
        return trading_set_from_legacy(("orders.market",))

    def connect(self) -> None: ...

    def disconnect(self) -> None: ...

    def health(self) -> tuple[bool, str]:
        return True, "ok"


def test_static_plugin_fail_closed_face_access() -> None:
    plugin = StaticPlugin(
        name="fake",
        display_name="Fake",
        face_map={Domain.TRADING: _FakeTrading()},
        capabilities=trading_set_from_legacy(("orders.market",)),
    )
    assert plugin.faces() == (Domain.TRADING,)
    assert plugin.face(Domain.TRADING) is not None
    with pytest.raises(UnsupportedCapabilityError):
        plugin.face(Domain.HISTORICAL_DATA)
    with pytest.raises(UnsupportedCapabilityError):
        plugin.face(Domain.MARKET_DATA)


def test_factory_plugin_passes_settings_and_reports_undeclared_caps_honestly() -> None:
    built: list[object] = []

    def make_hist(settings: object) -> object:
        built.append(settings)
        return object()

    plugin = FactoryPlugin(
        name="legacy",
        display_name="Legacy",
        factories={Domain.HISTORICAL_DATA: make_hist},
        capabilities=None,
    )
    settings = object()
    assert plugin.face(Domain.HISTORICAL_DATA, settings) is not None
    assert built == [settings]  # settings passed through (legacy ProviderFactory shape)
    assert plugin.capability_set().items == frozenset()  # honest: undeclared = nothing


def test_static_plugin_validation() -> None:
    with pytest.raises(ValueError):
        StaticPlugin("x", "X", {}, trading_set_from_legacy(("orders.market",)))
    with pytest.raises(ValueError):
        StaticPlugin(
            "x",
            "X",
            {"warp": object()},  # type: ignore[dict-item]
            trading_set_from_legacy(("orders.market",)),
        )


# ── M2: registry ──────────────────────────────────────────────────────────


def _record(name: str, domains: tuple[Domain, ...] = (Domain.TRADING,)) -> BrokerRecord:
    return BrokerRecord(
        name=name,
        display_name=name.title(),
        plugin=StaticPlugin(
            name=name,
            display_name=name.title(),
            face_map={Domain.TRADING: _FakeTrading()},
            capabilities=trading_set_from_legacy(("orders.market",)),
        ),
        capabilities=trading_set_from_legacy(("orders.market",)),
        faces=domains,
    )


def test_registry_register_get_list_find() -> None:
    registry = BrokerRegistry()
    registry.register(_record("beta"))
    registry.register(_record("alpha", (Domain.HISTORICAL_DATA,)))
    assert registry.names() == ("alpha", "beta")  # sorted, order independent
    assert registry.get("beta").name == "beta"
    assert "beta" in registry and "gamma" not in registry
    assert [r.name for r in registry.find_with(Caps.ORDERS_MARKET)] == ["alpha", "beta"]
    assert [r.name for r in registry.find_with_domain(Domain.HISTORICAL_DATA)] == ["alpha"]


def test_registry_duplicate_registration_fails_closed() -> None:
    registry = BrokerRegistry()
    registry.register(_record("dupe"))
    with pytest.raises(DuplicateBrokerError):
        registry.register(_record("dupe"))
    assert len(registry) == 1  # original untouched


def test_registry_unknown_name_fails_closed() -> None:
    registry = BrokerRegistry()
    with pytest.raises(BrokerNotRegisteredError):
        registry.get("ghost")
    with pytest.raises(BrokerNotRegisteredError):
        registry.unregister("ghost")


def test_registry_record_validation() -> None:
    with pytest.raises(ValueError):
        _record("")
    with pytest.raises(ValueError):
        BrokerRecord(
            name="x",
            display_name="X",
            plugin=object(),  # type: ignore[arg-type]
            capabilities=CapabilitySet(),
            faces=(),
        )


# ── M3: selection contract ────────────────────────────────────────────────


def _selection(name: str = "venue") -> BrokerSelection:
    return BrokerSelection(
        name=name, environment=Environment.SANDBOX, selected_at=TS, reason="user-selected"
    )


def test_broker_selection_validation() -> None:
    assert _selection().name == "venue"
    with pytest.raises(SelectionError):
        BrokerSelection("", Environment.PAPER, TS, "r")
    with pytest.raises(SelectionError):
        BrokerSelection("x", "live", TS, "r")  # type: ignore[arg-type]
    with pytest.raises(SelectionError):
        BrokerSelection("x", Environment.PAPER, "not-a-date", "r")
    with pytest.raises(SelectionError):
        BrokerSelection("x", Environment.PAPER, TS, "  ")


def test_selection_store_roundtrip_and_clear() -> None:
    store = MemorySelectionStore()
    assert store.load() is None
    store.save(_selection())
    assert store.load() is not None and store.load().name == "venue"  # type: ignore[union-attr]
    store.clear()
    assert store.load() is None


def test_surface_resolution_fail_closed_matrix() -> None:
    hist = capability_set({Domain.HISTORICAL_DATA: (Caps.HIST_CANDLES,)})
    # No selection → fail-closed everywhere (§8: no silent default)
    assert surface_resolution(None, hist, Domain.HISTORICAL_DATA) == (
        False,
        "no broker selected",
    )
    sel = _selection()
    # Selection without the domain → not allowed, explicit override language
    allowed, reason = surface_resolution(sel, hist, Domain.TRADING)
    assert allowed is False and "does not provide trading capability" in reason
    # Selection with the domain → allowed
    allowed, reason = surface_resolution(sel, hist, Domain.HISTORICAL_DATA)
    assert allowed is True
    # Undeclared capabilities → honest, never assumed
    assert surface_resolution(sel, None, Domain.HISTORICAL_DATA) == (
        False,
        "broker 'venue' capabilities are undeclared",
    )
