"""Phase 3 provider mapping — canonical stays truth, providers map outside.

Covers the §21 matrix (registration, both providers, both directions,
duplicates, conflicts, unknown/ambiguous/inactive/disabled, restart,
corrupt storage, no silent canonical creation, strategy independence)
plus the §22 static architecture proofs (no provider coupling in
CanonicalInstrument, no SDK imports in strategy code, adapters as the
only interpreter of provider identity).
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

import pytest  # noqa: E402

from strategy.instrument_registry import reset_instrument_registry  # noqa: E402
from strategy.models.provider_instrument import (  # noqa: E402
    DISABLED,
    FOUND,
    MAPPED,
    NOT_FOUND,
    UNMAPPED,
    ProviderInstrument,
)
from strategy.provider_adapters import (  # noqa: E402
    ProviderParseError,
    parse_fyers_symbol,
    parse_zerodha_record,
)
from strategy.provider_mapping import (  # noqa: E402
    MappingError,
    ProviderMappingRegistry,
)

STRAT_ROOT = ROOT / "src" / "strategy"


@pytest.fixture(autouse=True)
def _isolated_registry():
    reset_instrument_registry()
    yield
    reset_instrument_registry()


def _registry(tmp_path: Path | None = None) -> ProviderMappingRegistry:
    if tmp_path is None:
        return ProviderMappingRegistry()
    return ProviderMappingRegistry(tmp_path / "instruments" / "provider_mappings.json")


_FYERS_ROWS = ["NSE:RELIANCE-EQ", "NSE:TCS-EQ", "NSE:SBIN-EQ"]
_ZERODHA_ROWS = [
    {"tradingsymbol": "RELIANCE", "exchange": "NSE", "segment": "NSE", "instrument_token": 738561},
    {"tradingsymbol": "INFY", "exchange": "NSE", "segment": "NSE", "instrument_token": 408065},
]


def test_01_provider_instrument_registers() -> None:
    registry = _registry()
    record = registry.register_provider_instrument(
        "FYERS", "NSE:RELIANCE-EQ", "NSE:EQUITY:RELIANCE", source="unit"
    )
    assert record.provider == "FYERS"
    assert record.canonical_instrument_id == "NSE:EQUITY:RELIANCE"
    assert record.symbol == "RELIANCE"
    assert registry.mapping_status("FYERS")["mapped"] == 1


def test_02_canonical_stays_provider_independent() -> None:
    import strategy.models.instrument as canonical_module

    # Exact field set: identity + lifecycle, no provider slots to fill.
    fields = set(canonical_module.CanonicalInstrument.__dataclass_fields__)
    assert fields == {
        "instrument_id",
        "exchange",
        "segment",
        "symbol",
        "instrument_type",
        "status",
    }
    # No provider SDK coupling: no vendor-named imports anywhere.
    source = Path(canonical_module.__file__ or "").read_text(encoding="utf-8")
    assert "fyers_apiv3" not in source
    assert "kiteconnect" not in source
    assert "import fyers" not in source
    assert "import kite" not in source


def test_03_fyers_mapping_works() -> None:
    registry = _registry()
    report = registry.ingest_master("FYERS", list(_FYERS_ROWS), source="fyers-master")
    assert (report.total, report.mapped, report.unmapped) == (3, 3, 0)
    lookup = registry.get_mapping("NSE:EQUITY:SBIN", "FYERS")
    assert lookup.status == FOUND
    assert lookup.provider_instrument is not None
    assert lookup.provider_instrument.provider_instrument_id == "NSE:SBIN-EQ"


def test_04_zerodha_mapping_works() -> None:
    registry = _registry()
    report = registry.ingest_master("ZERODHA", list(_ZERODHA_ROWS), source="kite-master")
    assert (report.total, report.mapped) == (2, 2)
    outcome = registry.resolve_provider_instrument("ZERODHA", "738561")
    assert outcome.status == MAPPED
    assert outcome.canonical_instrument_id == "NSE:EQUITY:RELIANCE"


def test_05_reverse_resolution_works() -> None:
    registry = _registry()
    registry.ingest_master("FYERS", list(_FYERS_ROWS))
    registry.ingest_master("ZERODHA", list(_ZERODHA_ROWS))
    status, instrument = registry.resolve_provider_to_canonical("FYERS", "NSE:TCS-EQ")
    assert status == MAPPED
    assert instrument is not None and instrument.symbol == "TCS"
    status, instrument = registry.resolve_provider_to_canonical("ZERODHA", "408065")
    assert status == MAPPED
    assert instrument is not None and instrument.symbol == "INFY"


def test_06_canonical_to_provider_resolution() -> None:
    registry = _registry()
    registry.ingest_master("ZERODHA", list(_ZERODHA_ROWS))
    assert registry.get_provider_mapping("NSE:EQUITY:INFY", "ZERODHA").status == FOUND
    assert registry.get_mapping("NSE:EQUITY:INFY", "FYERS").status == NOT_FOUND
    assert registry.get_mapping("NSE:EQUITY:SBIN", "ZERODHA").status == NOT_FOUND


def test_07_duplicate_provider_id_rejected() -> None:
    registry = _registry()
    registry.register_provider_instrument("FYERS", "NSE:SBIN-EQ", "NSE:EQUITY:SBIN")
    with pytest.raises(MappingError):
        registry.register_provider_instrument("FYERS", "NSE:SBIN-EQ", "NSE:EQUITY:INFY")
    # Original survives untouched.
    assert (
        registry.resolve_provider_instrument("FYERS", "NSE:SBIN-EQ").canonical_instrument_id
        == "NSE:EQUITY:SBIN"
    )


def test_08_second_active_mapping_for_same_pair_rejected() -> None:
    registry = _registry()
    registry.ingest_master("FYERS", ["NSE:SBIN-EQ"])
    report = registry.ingest_master("FYERS", ["NSE:SBIN-BE"])
    assert report.conflicting == 1
    assert report.mapped == 0
    lookup = registry.get_mapping("NSE:EQUITY:SBIN", "FYERS")
    assert lookup.status == FOUND
    assert lookup.provider_instrument is not None
    assert lookup.provider_instrument.provider_instrument_id == "NSE:SBIN-EQ"


def test_09_conflict_detected_and_reported() -> None:
    registry = _registry()
    registry.register_provider_instrument("ZERODHA", "779521", "NSE:EQUITY:SBIN")
    with pytest.raises(MappingError, match="conflict"):
        registry.register_provider_instrument("ZERODHA", "779521", "NSE:EQUITY:INFY")
    outcome = registry.resolve_provider_instrument("ZERODHA", "779521")
    assert outcome.status == "CONFLICT"


def test_10_unknown_provider_id_returns_unmapped() -> None:
    registry = _registry()
    registry.ingest_master("FYERS", list(_FYERS_ROWS))
    assert registry.resolve_provider_instrument("FYERS", "NSE:NOPE-EQ").status == UNMAPPED
    assert registry.resolve_provider_instrument("ZERODHA", "1").status == UNMAPPED


def test_11_ambiguous_records_rejected() -> None:
    registry = _registry()
    # FYERS symbol without a series suffix cannot be placed in a segment.
    with pytest.raises(ProviderParseError):
        parse_fyers_symbol("NSE:SBIN")
    report = registry.ingest_master("FYERS", ["NSE:SBIN", "NSE:TCS-EQ"])
    assert report.ambiguous == 1
    assert report.mapped == 1
    assert registry.resolve_provider_instrument("FYERS", "NSE:SBIN").status == UNMAPPED


def test_12_inactive_provider_instrument_disabled_not_deleted() -> None:
    registry = _registry()
    record = registry.register_provider_instrument("FYERS", "NSE:SBIN-EQ", "NSE:EQUITY:SBIN")
    assert record.is_active
    disabled = registry.disable_mapping("FYERS", "NSE:SBIN-EQ")
    assert not disabled.is_active
    assert registry.resolve_provider_instrument("FYERS", "NSE:SBIN-EQ").status == DISABLED
    assert registry.get_mapping("NSE:EQUITY:SBIN", "FYERS").status == DISABLED
    # The record survives disabling and can be re-enabled.
    assert registry.enable_mapping("FYERS", "NSE:SBIN-EQ").is_active
    assert registry.resolve_provider_instrument("FYERS", "NSE:SBIN-EQ").status == MAPPED


def test_13_disabled_mapping_queries() -> None:
    registry = _registry()
    registry.ingest_master("ZERODHA", list(_ZERODHA_ROWS))
    registry.disable_mapping("ZERODHA", "738561")
    status = registry.mapping_status("ZERODHA")
    assert status["mapped"] == 1
    assert status["disabled"] == 1
    assert len(registry.list_mappings("ZERODHA")) == 1
    assert len(registry.list_mappings("ZERODHA", active_only=False)) == 2


def test_14_mappings_persist_across_restart(tmp_path: Path) -> None:
    first = _registry(tmp_path)
    first.ingest_master("FYERS", list(_FYERS_ROWS), source="fyers-master")
    first.ingest_master("ZERODHA", list(_ZERODHA_ROWS), source="kite-master")
    second = _registry(tmp_path)
    assert second.resolve_provider_instrument("FYERS", "NSE:SBIN-EQ").status == MAPPED
    assert second.get_mapping("NSE:EQUITY:INFY", "ZERODHA").status == FOUND
    status = second.mapping_status()
    assert status["mapped"] == 5
    assert status["imports"]["FYERS"]["total"] == 3
    assert status["imports"]["ZERODHA"]["source"] == "kite-master"


def test_15_corrupt_storage_fails_safely(tmp_path: Path) -> None:
    path = tmp_path / "instruments" / "provider_mappings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    registry = _registry(tmp_path)
    assert registry.list_mappings() == ()
    assert registry.resolve_provider_instrument("FYERS", "NSE:SBIN-EQ").status == UNMAPPED
    # No fake mappings constructed: a fresh ingest still works on the path.
    report = registry.ingest_master("FYERS", ["NSE:SBIN-EQ"])
    assert report.mapped == 1


def test_16_fyers_data_cannot_create_canonical(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    report = registry.ingest_master("FYERS", ["NSE:MADEUP-EQ"], source="fyers-master")
    assert report.mapped == 0
    assert report.unmapped == 1
    assert "NO_CANONICAL_MATCH" in report.details[0][2]
    from strategy.instrument_registry import get_instrument_registry

    assert get_instrument_registry().get("NSE:EQUITY:MADEUP") is None


def test_17_zerodha_data_cannot_create_canonical() -> None:
    registry = _registry()
    report = registry.ingest_master(
        "ZERODHA",
        [{"tradingsymbol": "MADEUP", "exchange": "NSE", "segment": "NSE", "instrument_token": 1}],
    )
    assert (report.mapped, report.unmapped) == (0, 1)
    assert registry.resolve_provider_instrument("ZERODHA", "1").status == UNMAPPED


def test_18_strategy_loads_without_mappings(tmp_path: Path) -> None:
    from app.services.live_trading_service import LiveTradingService

    data_dir = tmp_path / "data"
    strategy_dir = tmp_path / "strategies"
    strategy_dir.mkdir(parents=True, exist_ok=True)
    service = LiveTradingService(str(data_dir), str(strategy_dir))
    # Canonical identity must survive provider outages: strategy selection
    # and universe configuration need no mapping store at all.
    service.configure(strategy_name="Momentum")
    service.configure(symbols=("NSE:RELIANCE",))
    assert service.config.symbols == ("NSE:RELIANCE",)
    assert service.snapshot()["strategy"]["id"] == "Momentum"


def test_22_static_no_sdk_coupling() -> None:
    for module_file in (
        "models/instrument.py",
        "models/provider_instrument.py",
        "models/universe.py",
        "universe_store.py",
        "instrument_registry.py",
        "provider_mapping.py",
        "provider_adapters.py",
    ):
        source = (STRAT_ROOT / module_file).read_text(encoding="utf-8")
        assert "fyers_apiv3" not in source, module_file
        assert "kiteconnect" not in source, module_file
        assert "import fyers" not in source, module_file
        assert "import kite" not in source, module_file
    # Adapters are the only strategy files that interpret provider identity
    # (provider NAMES as data, never SDK objects — proven above).
    for module_file in (
        "models/instrument.py",
        "models/universe.py",
        "universe_store.py",
        "instrument_registry.py",
        "provider_mapping.py",
        "registry.py",
        "runtime.py",
    ):
        source = (STRAT_ROOT / module_file).read_text(encoding="utf-8").lower()
        assert "fyers" not in source, module_file
        assert "zerodha" not in source, module_file


def test_adapter_shapes_match_repo_formats() -> None:
    # FYERS equity wire format used by the broker adapters.
    assert parse_fyers_symbol("NSE:SBIN-EQ") == ("NSE:SBIN-EQ", "NSE", "EQUITY", "SBIN")
    # Kite master row shape used by the Zerodha instruments fetcher.
    assert parse_zerodha_record(
        {"tradingsymbol": "sbin", "exchange": "NSE", "segment": "NSE", "instrument_token": 779521}
    ) == ("779521", "NSE", "EQUITY", "SBIN")
    with pytest.raises(ProviderParseError):
        parse_zerodha_record(
            {
                "tradingsymbol": "RELIANCE",
                "exchange": "NFO",
                "segment": "NFO",
                "instrument_token": 1,
            }
        )


def test_provider_record_validation() -> None:
    with pytest.raises(ValueError):
        ProviderInstrument(
            provider="FYERS",
            provider_instrument_id="",
            canonical_instrument_id="NSE:EQUITY:SBIN",
            exchange="NSE",
            segment="EQUITY",
            symbol="SBIN",
        )
    with pytest.raises(MappingError):
        _registry().register_provider_instrument("FYERS", "NSE:X-EQ", "NSE:EQUITY:NOPE")
    with pytest.raises(MappingError):
        _registry().ingest_master("UNKNOWN", [])
