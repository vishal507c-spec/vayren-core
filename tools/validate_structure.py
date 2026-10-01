"""validate_structure.py — updated for src/ layout (Phase 5)."""

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
        "provider/factory.py",
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
        "common/__init__.py",
        "common/credentials.py",
        "common/credentials_store.py",
        "common/manager.py",
        "common/selenium_driver.py",
        "common/throttle.py",
        "interfaces/__init__.py",
        "interfaces/faces.py",
        "interfaces/historical.py",
        "providers/__init__.py",
        "providers/zerodha/__init__.py",
        "providers/zerodha/adapter.py",
        "providers/zerodha/auth.py",
        "providers/zerodha/credentials.py",
        "providers/zerodha/fetch.py",
        "providers/zerodha/instruments.py",
        "providers/zerodha/live_activation.py",
        "providers/zerodha/live_auth.py",
        "providers/zerodha/live_market_data.py",
        "providers/zerodha/live_trading.py",
        "providers/zerodha/spec.py",
        "providers/fyers/__init__.py",
        "providers/fyers/adapter.py",
        "providers/fyers/auto_auth.py",
        "providers/fyers/credentials.py",
        "providers/fyers/live_auth.py",
        "providers/fyers/selenium_auth.py",
        "providers/fyers/session_adapter.py",
        "providers/fyers/spec.py",
        "providers/skeleton/__init__.py",
        "tests/__init__.py",
    ],
}

# All domains live directly under src/ after Phase 5 migration.
SRC_DIR = ROOT / "src"


def main() -> int:
    errors: list[str] = []
    for domain, required in DOMAIN_STRUCTURE.items():
        domain_path = SRC_DIR / domain
        if not domain_path.is_dir():
            errors.append(f"Missing domain directory: src/{domain}/")
            continue
        for item in required:
            item_path = domain_path / item
            if not item_path.exists():
                errors.append(f"Missing: src/{domain}/{item}")
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
