"""Phase-3 service wiring — broker capital feeds the risk engine.

* LIVE + connected broker funds → engine carries broker available capital,
  sizing READY, snapshot exposes the full pipeline.
* LIVE + connected broker without funds → NOT READY, engine zeroed, START
  blocked with a capital reason (never a silent fallback).
* PAPER → simulated basis, sizing READY.
* Watchlist sizing runs only on valid capital (no fake qty).
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from app.services.live_trading_service import LiveTradingService  # noqa: E402


def _service(tmp_path: Path) -> LiveTradingService:
    return LiveTradingService(data_dir=str(tmp_path), strategy_dir=str(tmp_path))


def _view(*, connected=True, available=None) -> dict:
    funds = {} if available is None else {"available": available, "used": 0.0, "total": available}
    return {
        "id": "fyers",
        "display": "FYERS",
        "connected": connected,
        "status": "CONNECTED" if connected else "DISCONNECTED",
        "reason": "",
        "account_id": "ACC1" if connected else "",
        "configured": True,
        "funds": funds,
    }


def test_live_feeds_broker_capital_into_engine(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.configure(mode="LIVE")
    service._refresh_risk_capital(_view(available=100_000.0))
    assert service._risk_capital is not None
    assert service._risk_capital.available == 100_000.0
    assert service._risk_capital.source == "broker"
    assert service._risk_engine.raw_capital == 100_000.0
    assert service._risk_engine.effective_capital == 400_000.0
    assert service._risk_engine.max_allowed_risk == 600.0
    assert service._risk_status == "READY"


def test_live_without_funds_is_not_ready_and_zeroed(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.configure(mode="LIVE")
    service._refresh_risk_capital(_view(available=None))
    assert service._risk_capital is None
    assert service._risk_engine.raw_capital == 0.0
    assert service._risk_status == "NOT READY"
    assert service._risk_reason == "BROKER_CAPITAL_UNAVAILABLE"


def test_paper_uses_simulated_basis(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.configure(mode="PAPER")
    service._refresh_risk_capital(_view(connected=False))
    assert service._risk_capital is not None
    assert service._risk_capital.source == "paper"
    assert service._risk_status == "READY"


def test_live_blocker_names_missing_capital(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.configure(
        strategy_name="OBR",
        symbols=("NSE:RELIANCE",),
        timeframe="30m",
        quantity=10.0,
        mode="LIVE",
    )
    service._broker_manager = None
    service._selection = SimpleNamespace(current=lambda: SimpleNamespace(name="fyers"))
    # Simulate a connected venue whose funds leg is missing.
    service._broker_view = lambda: _view(connected=True, available=None)  # type: ignore[method-assign]
    with patch.object(LiveTradingService, "_resolve_live_venue_id", return_value="fyers-live"):
        blockers = service._live_blockers()
    assert any("broker capital unavailable" in b for b in blockers), blockers
