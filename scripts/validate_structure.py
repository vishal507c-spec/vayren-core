"""Validate that the repository structure follows the blueprint.

Domains live inside numbered startup-flow chapters (NN_CHAPTER/domain/).
Knowledge in 90_brain is excluded.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

DOMAIN_STRUCTURE: dict[str, list[str]] = {
    "app": [
        "__init__.py",
        "README.md",
        "headless.py",
        "tests/__init__.py",
    ],
    "core": [
        "__init__.py",
        "ai/__init__.py",
        "ai/boundary.py",
        "ai/change_simulation.py",
        "ai/context.py",
        "ai/intent.py",
        "ai/memory/__init__.py",
        "ai/memory/engineering.py",
        "ai/memory/performance.py",
        "ai/optimization.py",
        "ai/plan.py",
        "ai/plan_validator.py",
        "ai/providers.py",
        "ai/sandbox.py",
        "events/__init__.py",
        "events/event.py",
        "native/__init__.py",
        "native/loader.py",
        "tests/__init__.py",
    ],
    "market": [
        "__init__.py",
        "models/__init__.py",
        "models/bar.py",
        "native_aggregate.py",
        "native_bar.py",
        "native_timeframe.py",
    ],
    "data": [
        "__init__.py",
        "settings.py",
        "throttle.py",
        "native_download.py",
        "provider/__init__.py",
        "provider/contract.py",
        "provider/credentials.py",
        "provider/credentials_store.py",
        "provider/factory.py",
        "provider/manager.py",
        "provider/selenium_driver.py",
        "provider/zerodha/__init__.py",
        "provider/zerodha/adapter.py",
        "provider/zerodha/auth.py",
        "provider/zerodha/credentials.py",
        "provider/zerodha/fetch.py",
        "provider/zerodha/instruments.py",
        "provider/zerodha/live_activation.py",
        "provider/zerodha/live_auth.py",
        "provider/zerodha/live_market_data.py",
        "provider/zerodha/live_trading.py",
        "provider/fyers/__init__.py",
        "provider/fyers/adapter.py",
        "provider/fyers/auto_auth.py",
        "provider/fyers/credentials.py",
        "provider/fyers/live_auth.py",
        "provider/fyers/selenium_auth.py",
        "provider/fyers/session_adapter.py",
        "tests/__init__.py",
    ],
    "strategy": [
        "__init__.py",
        "README.md",
        "manifest.py",
        "registry.py",
        "runtime.py",
        "version.py",
        "models/__init__.py",
        "events/__init__.py",
        "research/__init__.py",
        "strategies/__init__.py",
        "tests/__init__.py",
    ],
    "backtest": [
        "__init__.py",
        "native_metrics.py",
        "native_positions.py",
        "native_replay.py",
        "native_runner.py",
        "native_validation.py",
    ],
    "risk": [
        "__init__.py",
        "native_engine.py",
        "native_kill_switch.py",
        "native_session.py",
    ],
    "execution": [
        "__init__.py",
        "models/__init__.py",
        "models/order_state.py",
        "events/__init__.py",
        "market_data/native_normalizer.py",
        "broker/native_policy.py",
        "native_execution.py",
        "native_order_state.py",
        "regime.py",
        "adaptive/__init__.py",
        "adaptive/attention.py",
        "adaptive/memory.py",
        "adaptive/confidence.py",
        "ml_interfaces.py",
    ],
    "broker": [
        "__init__.py",
        "README.md",
        "vocab.py",
        "capabilities.py",
        "faces.py",
        "funds.py",
        "credentials.py",
        "health.py",
        "identity.py",
        "registry.py",
        "selection.py",
        "selection_store.py",
        "adapters/__init__.py",
        "adapters/zerodha/__init__.py",
        "adapters/skeleton/__init__.py",
        "tests/__init__.py",
    ],
}

# Map domain names to their numbered chapter folders
DOMAIN_CHAPTERS: dict[str, str] = {
    "app": "00_app",
    "core": "01_core",
    "market": "03_market",
    "data": "02_data",
    "strategy": "05_strategy",
    "backtest": "06_backtest",
    "risk": "07_risk",
    "execution": "08_execution",
    "broker": "09_broker",
}


def main() -> int:
    errors: list[str] = []
    for domain, required in DOMAIN_STRUCTURE.items():
        chapter = DOMAIN_CHAPTERS[domain]
        domain_path = ROOT / chapter / domain
        if not domain_path.is_dir():
            errors.append(f"Missing domain directory: {chapter}/{domain}/")
            continue
        for item in required:
            item_path = domain_path / item
            if not item_path.exists():
                errors.append(f"Missing: {chapter}/{domain}/{item}")
    if errors:
        print("Structure validation FAILED:")
        for e in errors:
            print(f"  - {e}")
        return 1
    print("Structure validation PASSED")
    print(f"  {len(DOMAIN_STRUCTURE)} domains checked")
    return 0


if __name__ == "__main__":
    exit(main())
