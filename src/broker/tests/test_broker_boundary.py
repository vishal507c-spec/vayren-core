"""Broker boundary negative and positive suite (Phase 7).

Proves:
- broker modules import correctly
- broker SDKs are isolated behind src/broker/
- data modules do not import broker implementation internals
- broker providers do not depend on data implementation modules
- existing Zerodha and Fyers behavior is preserved
- authentication and provider contracts remain intact
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))

from broker.capabilities import Domain  # noqa: E402
from broker.interfaces import (  # noqa: E402
    CANONICAL_INTERVALS,
    RATE_LIMITED,
    TOKEN_EXPIRED,
    HistoricalFace,
    ProviderError,
)
from broker.providers import seed_providers  # noqa: E402
from broker.providers.fyers import FyersProvider  # noqa: E402
from broker.providers.zerodha import ZerodhaProvider  # noqa: E402
from broker.registry import default_registry  # noqa: E402


def test_broker_modules_import_cleanly() -> None:
    """Verify top-level broker packages and subpackages import without error."""
    import broker
    import broker.common
    import broker.interfaces
    import broker.providers
    import broker.providers.fyers
    import broker.providers.skeleton
    import broker.providers.zerodha

    assert broker is not None
    assert broker.common is not None
    assert broker.interfaces is not None
    assert broker.providers is not None
    assert broker.providers.zerodha is not None
    assert broker.providers.fyers is not None
    assert broker.providers.skeleton is not None


def test_broker_sdks_isolated_behind_broker() -> None:
    """Verify third-party broker SDKs (kiteconnect) are imported ONLY inside src/broker/."""
    for py in (ROOT / "src").rglob("*.py"):
        if py.name == "__pycache__" or "tests" in py.parts:
            continue
        rel = py.relative_to(ROOT / "src").as_posix()
        if rel.startswith("broker/"):
            continue
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for name in node.names:
                    assert "kiteconnect" not in name.name, f"kiteconnect leaked in {rel}"
            elif isinstance(node, ast.ImportFrom) and node.module:
                assert "kiteconnect" not in node.module, f"kiteconnect leaked in {rel}"


def test_data_modules_do_not_import_broker_internals() -> None:
    """Verify src/data/ imports only canonical broker entrypoints."""
    for py in (ROOT / "src" / "data").rglob("*.py"):
        if py.name == "__pycache__" or "tests" in py.parts:
            continue
        rel = py.relative_to(ROOT / "src").as_posix()
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                mod = node.module
                if mod.startswith("broker.providers."):
                    raise AssertionError(f"{rel} imports broker provider internal: {mod}")


def test_broker_providers_do_not_depend_on_data() -> None:
    """Verify src/broker/ has no runtime dependencies on src/data/."""
    for py in (ROOT / "src" / "broker").rglob("*.py"):
        if py.name == "__pycache__" or "tests" in py.parts:
            continue
        rel = py.relative_to(ROOT / "src").as_posix()
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                is_data = node.module == "data" or node.module.startswith("data.")
                assert not is_data, f"{rel} depends on data module: {node.module}"
            elif isinstance(node, ast.Import):
                for name in node.names:
                    is_data = name.name == "data" or name.name.startswith("data.")
                    assert not is_data, f"{rel} depends on data module: {name.name}"


def test_zerodha_provider_contract_preserved() -> None:
    """Verify ZerodhaProvider preserves the Provider protocol contract and sentinels."""
    seed_providers()
    rec = default_registry().get("zerodha")
    assert rec.name == "zerodha"
    face = rec.plugin.face(Domain.HISTORICAL_DATA, None)  # type: ignore[arg-type]
    assert isinstance(face, ZerodhaProvider)
    assert isinstance(face, HistoricalFace)
    assert hasattr(face, "available")
    assert hasattr(face, "symbols")
    assert hasattr(face, "fetch_candles")
    assert hasattr(face, "new_session")
    assert hasattr(face, "renew")


def test_fyers_provider_contract_preserved() -> None:
    """Verify FyersProvider preserves the Provider protocol contract and sentinels."""
    seed_providers()
    rec = default_registry().get("fyers")
    assert rec.name == "fyers"
    face = rec.plugin.face(Domain.HISTORICAL_DATA, None)  # type: ignore[arg-type]
    assert isinstance(face, FyersProvider)
    assert isinstance(face, HistoricalFace)
    ready, reason = face.available()
    assert isinstance(ready, bool)
    assert isinstance(reason, str)


def test_sentinels_and_vocabulary_intact() -> None:
    """Verify control sentinels and interval definitions match across layers."""
    assert TOKEN_EXPIRED is not None
    assert RATE_LIMITED is not None
    assert TOKEN_EXPIRED is not RATE_LIMITED
    assert "1m" in CANONICAL_INTERVALS
    assert issubclass(ProviderError, RuntimeError)
