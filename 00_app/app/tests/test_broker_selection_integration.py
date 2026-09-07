"""M4 integration: selection → registry → historical/trading resolution.

Proves the authoritative selection drives every surface exactly the way
bootstrap wires it, without spinning the full Qt application.
"""

from __future__ import annotations

from pathlib import Path

import data.provider.factory  # noqa: E402,F401  (seeds zerodha)
import execution.broker.factory  # noqa: E402,F401  (seeds paper/sandbox)
from broker.capabilities import Domain
from data.provider.contract import Provider
from data.provider.factory import build_provider, unavailable_provider
from data.settings import DownloadSettings

from app.services.broker_selection_service import BrokerSelectionService, app_selection_store


def _resolve_history_provider(service: BrokerSelectionService, tmp_path: Path) -> Provider:
    """Mirror bootstrap's historical derivation (selection-driven)."""
    selection = service.current()
    allowed, reason = service.surface_allowed(Domain.HISTORICAL_DATA)
    settings = DownloadSettings(data_dir=str(tmp_path), provider=selection.name)
    if allowed:
        return build_provider(settings)
    return unavailable_provider(reason)


def test_selection_drives_historical_provider(tmp_path: Path) -> None:
    service = BrokerSelectionService(app_selection_store(tmp_path))
    provider = _resolve_history_provider(service, tmp_path)
    assert type(provider).__name__ == "ZerodhaProvider"  # compatibility default


def test_changing_selection_changes_resolved_provider(tmp_path: Path) -> None:
    service = BrokerSelectionService(app_selection_store(tmp_path))
    service.select("sandbox")  # trading-only broker, no historical face
    provider = _resolve_history_provider(service, tmp_path)
    assert type(provider).__name__ == "_UnavailableProvider"
    ready, reason = provider.available()
    assert ready is False
    assert "does not provide historical_data" in reason


def test_unsupported_history_fails_closed_no_fallback(tmp_path: Path) -> None:
    """A trading-only selection must NOT silently download via zerodha."""
    service = BrokerSelectionService(app_selection_store(tmp_path))
    service.select("sandbox")
    allowed, reason = service.surface_allowed(Domain.HISTORICAL_DATA)
    assert allowed is False
    provider = _resolve_history_provider(service, tmp_path)
    try:
        provider.symbols()
        raise AssertionError("unavailable provider must refuse")
    except Exception as exc:
        assert "historical_data" in str(exc)


def test_trading_resolution_preserves_paper_downgrade(tmp_path: Path) -> None:
    """Selection never bypasses mode resolution: paper stays paper."""
    from execution.broker.factory import resolve_broker
    from execution.broker.paper import PaperBroker
    from execution.modes import ExecutionMode, ModeGates

    service = BrokerSelectionService(app_selection_store(tmp_path))
    service.select("zerodha")  # no trading face
    allowed, _ = service.surface_allowed(Domain.TRADING)
    assert allowed is False  # honest: zerodha cannot trade
    broker, mode, _ = resolve_broker(ExecutionMode.PAPER, ModeGates())
    assert isinstance(broker, PaperBroker)  # paper path intact regardless
    broker.disconnect()


def test_paper_and_sandbox_resolve_through_registry() -> None:
    """Built-ins are reachable via the unified registry (M2 exposure)."""
    from broker.registry import default_registry
    from execution.broker.paper import PaperBroker
    from execution.broker.sandbox import SandboxBroker

    registry = default_registry()
    paper = registry.get("paper").plugin.face(Domain.TRADING)
    sandbox = registry.get("sandbox").plugin.face(Domain.TRADING)
    assert isinstance(paper, PaperBroker)
    assert isinstance(sandbox, SandboxBroker)
    paper.disconnect()
    sandbox.disconnect()
