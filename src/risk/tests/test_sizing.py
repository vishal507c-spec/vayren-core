"""Phase-3 sizing pipeline tests — broker capital × 4 × 0.15% → quantity.

Pins the exact formula, every structured denial, the final planned-risk
gate and the edge/property cases from the Phase-3 spec. Pure stdlib (no
native bridge), so these run in the fast inner loop.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from risk.sizing import (  # noqa: E402
    BROKER_CAPITAL_UNAVAILABLE,
    INVALID_ENTRY_PRICE,
    INVALID_STOP_PRICE,
    PLANNED_RISK_EXCEEDED,
    READY,
    RISK_BUDGET_TOO_SMALL_FOR_ONE_SHARE,
    STALE_CAPITAL,
    ZERO_RISK_PER_SHARE,
    BrokerCapital,
    size_position,
    validate_planned_quantity,
)

NOW = 1_700_000_000.0


def _capital(available, *, fetched=NOW, source="broker") -> BrokerCapital:
    return BrokerCapital(available=available, fetched_epoch=fetched, source=source)


def _size(available, entry, stop, **kwargs):
    kwargs.setdefault("now_epoch", NOW)
    return size_position(
        broker_capital=_capital(available), entry_price=entry, stop_price=stop, **kwargs
    )


# ── §21.1: ₹1,00,000 capital → ₹600 max risk ──────────────────────────────


def test_lakh_capital_gives_six_hundred_max_risk() -> None:
    verdict = _size(100_000.0, 1218.50, 1245.00)
    assert verdict.ready and verdict.reason == READY
    assert verdict.broker_capital == 100_000.0
    assert verdict.effective_capital == 400_000.0
    assert verdict.max_risk_per_stock == 600.0


# ── §21.2–4: KAYNES example → qty 22, risk/share 26.50, planned 583 ──────


def test_kaynes_example_quantity_22() -> None:
    verdict = _size(100_000.0, 1218.50, 1245.00)
    assert verdict.quantity == 22
    assert verdict.risk_per_share == 26.50
    assert verdict.planned_risk == 583.0


# ── §21.5–7: zero / unavailable / negative capital blocks ────────────────


def test_zero_capital_blocks() -> None:
    verdict = _size(0.0, 1218.50, 1245.00)
    assert not verdict.ready
    assert verdict.reason == BROKER_CAPITAL_UNAVAILABLE
    assert verdict.quantity == 0


def test_missing_capital_blocks() -> None:
    verdict = size_position(
        broker_capital=None, entry_price=1218.50, stop_price=1245.00, now_epoch=NOW
    )
    assert not verdict.ready
    assert verdict.reason == BROKER_CAPITAL_UNAVAILABLE
    assert verdict.quantity == 0


def test_negative_capital_blocks() -> None:
    verdict = _size(-50_000.0, 1218.50, 1245.00)
    assert not verdict.ready
    assert verdict.reason == BROKER_CAPITAL_UNAVAILABLE


def test_nan_capital_blocks() -> None:
    verdict = _size(float("nan"), 1218.50, 1245.00)
    assert not verdict.ready
    assert verdict.reason == BROKER_CAPITAL_UNAVAILABLE


# ── §21.8–10: entry == stop / entry 0 / stop 0 block ─────────────────────


def test_entry_equals_stop_blocks() -> None:
    verdict = _size(100_000.0, 1200.0, 1200.0)
    assert not verdict.ready
    assert verdict.reason == ZERO_RISK_PER_SHARE
    assert verdict.quantity == 0


def test_zero_entry_blocks() -> None:
    verdict = _size(100_000.0, 0.0, 1245.00)
    assert not verdict.ready
    assert verdict.reason == INVALID_ENTRY_PRICE


def test_zero_stop_blocks() -> None:
    verdict = _size(100_000.0, 1218.50, 0.0)
    assert not verdict.ready
    assert verdict.reason == INVALID_STOP_PRICE


def test_negative_prices_block() -> None:
    assert _size(100_000.0, -5.0, 1245.00).reason == INVALID_ENTRY_PRICE
    assert _size(100_000.0, 1218.50, -5.0).reason == INVALID_STOP_PRICE


# ── §21.11–13: qty zero blocks; FLOOR never CEIL ─────────────────────────


def test_budget_too_small_for_one_share_blocks() -> None:
    verdict = _size(100.0, 1218.50, 1245.00)  # max risk ₹0.60 < ₹26.50/share
    assert not verdict.ready
    assert verdict.reason == RISK_BUDGET_TOO_SMALL_FOR_ONE_SHARE


def test_quantity_floors_never_ceils() -> None:
    # 600 / 26.50 = 22.64… → 22, never 23.
    verdict = _size(100_000.0, 1218.50, 1245.00)
    assert verdict.quantity == 22 == math.floor(600.0 / 26.50)
    # Boundary probe: exact integer stays, dust above stays below.
    exact = _size(100_000.0, 100.0, 106.0)  # 600 / 6 = 100 exactly
    assert exact.quantity == 100
    assert exact.planned_risk == 600.0


# ── §21.14: planned risk never exceeds max ───────────────────────────────


def test_planned_risk_never_exceeds_max() -> None:
    verdict = _size(100_000.0, 1218.50, 1245.00)
    assert verdict.planned_risk is not None
    assert verdict.max_risk_per_stock is not None
    assert verdict.planned_risk <= verdict.max_risk_per_stock


# ── §21.15: stocks size independently ────────────────────────────────────


def test_stocks_size_independently() -> None:
    kaynes = _size(100_000.0, 1218.50, 1245.00)
    other = _size(100_000.0, 451.20, 462.30)
    assert kaynes.max_risk_per_stock == other.max_risk_per_stock == 600.0
    assert kaynes.planned_risk == 583.0
    assert other.quantity == math.floor(600.0 / abs(451.20 - 462.30))
    assert other.planned_risk is not None and other.planned_risk <= 600.0


# ── §21.16–18: mode boundaries ──────────────────────────────────────────


def test_live_rejects_non_broker_capital_source() -> None:
    verdict = size_position(
        broker_capital=_capital(100_000.0, source="configured"),
        entry_price=1218.50,
        stop_price=1245.00,
        mode="LIVE",
        now_epoch=NOW,
    )
    assert not verdict.ready
    assert verdict.reason == BROKER_CAPITAL_UNAVAILABLE


def test_paper_uses_simulated_capital() -> None:
    verdict = size_position(
        broker_capital=_capital(1_000_000.0, source="paper"),
        entry_price=1218.50,
        stop_price=1245.00,
        mode="PAPER",
        now_epoch=NOW,
    )
    assert verdict.ready
    assert verdict.max_risk_per_stock == 1_000_000.0 * 4.0 * 0.0015


def test_sandbox_uses_sandbox_capital() -> None:
    verdict = size_position(
        broker_capital=_capital(500_000.0, source="sandbox"),
        entry_price=100.0,
        stop_price=106.0,
        mode="SANDBOX",
        now_epoch=NOW,
    )
    assert verdict.ready
    assert verdict.max_risk_per_stock == 500_000.0 * 4.0 * 0.0015


# ── stale capital ────────────────────────────────────────────────────────


def test_stale_capital_blocks() -> None:
    verdict = size_position(
        broker_capital=_capital(100_000.0, fetched=NOW - 3600.0),
        entry_price=1218.50,
        stop_price=1245.00,
        mode="LIVE",
        now_epoch=NOW,
    )
    assert not verdict.ready
    assert verdict.reason == STALE_CAPITAL


def test_unknown_fetch_time_is_stale() -> None:
    verdict = size_position(
        broker_capital=BrokerCapital(available=100_000.0, fetched_epoch=None),
        entry_price=1218.50,
        stop_price=1245.00,
        mode="LIVE",
        now_epoch=NOW,
    )
    assert not verdict.ready
    assert verdict.reason == STALE_CAPITAL


def test_capital_changing_between_calculations() -> None:
    first = _size(100_000.0, 1218.50, 1245.00)
    second = _size(200_000.0, 1218.50, 1245.00)
    assert first.max_risk_per_stock == 600.0
    assert second.max_risk_per_stock == 1200.0
    assert second.quantity == math.floor(1200.0 / 26.50)


# ── §21.20 + §10: final gate rejects inflated quantity ──────────────────


def test_final_gate_passes_sized_quantity() -> None:
    verdict = _size(100_000.0, 1218.50, 1245.00)
    assert verdict.max_risk_per_stock is not None
    ok, reason = validate_planned_quantity(
        quantity=verdict.quantity,
        entry_price=1218.50,
        stop_price=1245.00,
        max_risk_per_stock=verdict.max_risk_per_stock,
    )
    assert ok and reason == READY


def test_final_gate_rejects_inflated_quantity() -> None:
    ok, reason = validate_planned_quantity(
        quantity=30,  # 30 × 26.50 = ₹795 > ₹600
        entry_price=1218.50,
        stop_price=1245.00,
        max_risk_per_stock=600.0,
    )
    assert not ok
    assert reason == PLANNED_RISK_EXCEEDED


def test_final_gate_rejects_fractional_or_nonpositive_quantity() -> None:
    from risk.sizing import QUANTITY_INVALID

    for bad in (0, -3, 22.5):
        ok, reason = validate_planned_quantity(
            quantity=bad,
            entry_price=1218.50,
            stop_price=1245.00,
            max_risk_per_stock=600.0,
        )
        assert not ok and reason == QUANTITY_INVALID


# ── §22: property / edge cases ──────────────────────────────────────────


def test_edge_matrix_planned_never_exceeds_max() -> None:
    capitals = (1.0, 100.0, 100_000.0, 10_000_000.0, 1_000_000_000.0)
    legs = (
        (1218.50, 1245.00),  # KAYNES
        (100.0, 100.01),  # tiny risk/share
        (100.0, 10_000.0),  # huge risk/share
        (1245.00, 1218.50),  # entry above stop
        (99.99, 100.005),  # decimal dust
        (600.0, 606.0),  # exact-boundary seeker
    )
    for capital in capitals:
        for entry, stop in legs:
            verdict = _size(capital, entry, stop)
            if verdict.ready:
                assert verdict.planned_risk is not None
                assert verdict.max_risk_per_stock is not None
                assert verdict.planned_risk <= verdict.max_risk_per_stock
                assert verdict.quantity >= 1
                assert verdict.risk_per_share == abs(entry - stop)
                assert verdict.quantity == math.floor(
                    verdict.max_risk_per_stock / abs(entry - stop)
                )
            else:
                assert verdict.reason in (
                    RISK_BUDGET_TOO_SMALL_FOR_ONE_SHARE,
                    ZERO_RISK_PER_SHARE,
                    INVALID_ENTRY_PRICE,
                    INVALID_STOP_PRICE,
                    BROKER_CAPITAL_UNAVAILABLE,
                    STALE_CAPITAL,
                )


def test_engine_projection_matches_sizing_pipeline() -> None:
    """The UI projection engine and the enforcement pipeline agree exactly."""
    import sys as _sys
    from pathlib import Path as _Path

    _root = _Path(__file__).resolve().parents[3]
    if str(_root / "src") not in _sys.path:
        _sys.path.insert(0, str(_root / "src"))
    from app.services.live_trading_service import CapitalRiskEngine

    engine = CapitalRiskEngine(raw_capital=100_000.0)
    qty, rps, planned, _util = engine.compute_qty(1218.50, 1245.00)
    verdict = _size(100_000.0, 1218.50, 1245.00)
    assert (qty, rps, planned) == (
        verdict.quantity,
        verdict.risk_per_share,
        verdict.planned_risk,
    )
    assert engine.effective_capital == verdict.effective_capital
    assert engine.max_allowed_risk == verdict.max_risk_per_stock
