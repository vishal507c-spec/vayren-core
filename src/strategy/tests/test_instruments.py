"""Canonical instrument registry + resolver (Phase 2).

Single authoritative identity path for NSE Equity: normalization,
resolution states, uniqueness, active/inactive lifecycle — with zero
broker/provider coupling (asserted, not assumed).
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

import pytest  # noqa: E402

from strategy.instrument_registry import (  # noqa: E402
    NSE_EQUITY_SEED,
    RegistryError,
    get_instrument_registry,
    reset_instrument_registry,
    resolve_instrument,
)
from strategy.models.instrument import (  # noqa: E402
    INACTIVE,
    INVALID_FORMAT,
    NOT_FOUND,
    RESOLVED,
    UNSUPPORTED_EXCHANGE,
    UNSUPPORTED_SEGMENT,
    CanonicalInstrument,
    canonical_id,
    parse_reference,
)


@pytest.fixture(autouse=True)
def _isolated_registry():
    reset_instrument_registry()
    yield
    reset_instrument_registry()


def test_01_nse_reliance_resolves() -> None:
    result = resolve_instrument("NSE:RELIANCE")
    assert result.status == RESOLVED
    assert result.resolved is not None
    assert result.resolved.instrument_id == "NSE:EQUITY:RELIANCE"
    assert result.resolved.exchange == "NSE"
    assert result.resolved.segment == "EQUITY"
    assert result.resolved.symbol == "RELIANCE"
    assert result.resolved.instrument_type == "EQUITY"
    assert result.resolved.status == "ACTIVE"
    assert result.resolved.display == "NSE:RELIANCE"


def test_02_lowercase_resolves_identically() -> None:
    assert (
        resolve_instrument("nse:reliance").resolved == resolve_instrument("NSE:RELIANCE").resolved
    )


def test_03_whitespace_normalizes() -> None:
    assert resolve_instrument("  NSE:RELIANCE  ").status == RESOLVED
    assert resolve_instrument("NSE : RELIANCE").status == RESOLVED


def test_04_invalid_format_rejected() -> None:
    for bad in ("", "   ", "RELIANCE", "NSE:", ":RELIANCE", "NSE:RELIANCE:X:Y", None, 42):
        assert resolve_instrument(bad).status == INVALID_FORMAT, bad


def test_05_unsupported_exchange_rejected() -> None:
    result = resolve_instrument("BSE:SBIN")
    assert result.status == UNSUPPORTED_EXCHANGE
    assert result.resolved is None


def test_06_unsupported_segment_rejected() -> None:
    assert resolve_instrument("NSE:FNO:RELIANCE").status == UNSUPPORTED_SEGMENT
    assert resolve_instrument("NFO:RELIANCE").status == UNSUPPORTED_EXCHANGE


def test_07_unknown_symbol_returns_not_found() -> None:
    result = resolve_instrument("NSE:ABCXYZ")
    assert result.status == NOT_FOUND
    assert result.resolved is None
    # Never invented: the registry still does not contain it.
    assert get_instrument_registry().get("NSE:EQUITY:ABCXYZ") is None


def test_08_inactive_symbol_returns_inactive() -> None:
    get_instrument_registry().set_status("NSE:EQUITY:TCS", "INACTIVE")
    result = resolve_instrument("NSE:TCS")
    assert result.status == INACTIVE
    assert result.resolved is None
    # The identity survives deactivation — it is not deleted.
    assert get_instrument_registry().get("NSE:EQUITY:TCS") is not None


def test_09_duplicate_canonical_instrument_prevented() -> None:
    registry = get_instrument_registry()
    before = len(registry)
    twin = CanonicalInstrument(
        instrument_id="NSE:EQUITY:RELIANCE",
        exchange="NSE",
        segment="EQUITY",
        symbol="RELIANCE",
        instrument_type="EQUITY",
    )
    registry.register(twin)  # identical record: idempotent, no growth
    assert len(registry) == before
    with pytest.raises(RegistryError):
        registry.register_display("NSE:FNO:RELIANCE")


def test_identity_coherence_enforced() -> None:
    with pytest.raises(ValueError):
        CanonicalInstrument(
            instrument_id="WRONG-ID",
            exchange="NSE",
            segment="EQUITY",
            symbol="RELIANCE",
            instrument_type="EQUITY",
        )
    with pytest.raises(ValueError):
        CanonicalInstrument(
            instrument_id="NSE:EQUITY:RELIANCE",
            exchange="NSE",
            segment="EQUITY",
            symbol="RELIANCE",
            instrument_type="EQUITY",
            status="DELISTED",
        )


def test_registry_scope_is_nse_equity() -> None:
    with pytest.raises(RegistryError):
        get_instrument_registry().register(
            CanonicalInstrument(
                instrument_id="BSE:EQUITY:SBIN",
                exchange="BSE",
                segment="EQUITY",
                symbol="SBIN",
                instrument_type="EQUITY",
            )
        )


def test_seed_covers_live_flow_vocabulary() -> None:
    for display in ("NSE:RELIANCE", "NSE:TCS", "NSE:INFY", "NSE:SBIN", "NSE:HDFCBANK"):
        assert display in NSE_EQUITY_SEED
        assert resolve_instrument(display).status == RESOLVED


def test_no_broker_identifiers_anywhere() -> None:
    instrument = resolve_instrument("NSE:RELIANCE").resolved
    assert instrument is not None
    fields = set(instrument.__dataclass_fields__)
    assert fields == {
        "instrument_id",
        "exchange",
        "segment",
        "symbol",
        "instrument_type",
        "status",
    }
    blob = repr(instrument).lower() + repr(get_instrument_registry().snapshot()).lower()
    for forbidden in ("fyers", "zerodha", "token", "provider", "broker", "vendor"):
        assert forbidden not in blob, forbidden


def test_canonical_id_is_deterministic_and_broker_free() -> None:
    assert canonical_id("NSE", "EQUITY", "RELIANCE") == "NSE:EQUITY:RELIANCE"
    assert parse_reference("NSE:RELIANCE") == ("NSE", "EQUITY", "RELIANCE")
    assert parse_reference("NSE:EQUITY:RELIANCE") == ("NSE", "EQUITY", "RELIANCE")
