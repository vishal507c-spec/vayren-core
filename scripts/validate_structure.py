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
        "README.md",
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
        "contracts/__init__.py",
        "event_bus/__init__.py",
        "events/__init__.py",
        "logger/__init__.py",
        "registry/__init__.py",
        "registry/capability_registry.py",
        "registry/component_registry.py",
        "system/__init__.py",
        "tests/__init__.py",
    ],
    "market": [
        "__init__.py",
        "README.md",
        "manifest.py",
        "models/__init__.py",
        "database/__init__.py",
        "repository/__init__.py",
        "loader/__init__.py",
        "events/__init__.py",
        "tests/__init__.py",
    ],
    "data": [
        "__init__.py",
        "README.md",
        "manifest.py",
        "worker.py",
        "settings.py",
        "calendar.py",
        "throttle.py",
        "lock.py",
        "models.py",
        "symbols.py",
        "reporter.py",
        "storage/__init__.py",
        "storage/candle_db.py",
        "storage/scanner.py",
        "provider/__init__.py",
        "provider/contract.py",
        "provider/factory.py",
        "provider/zerodha/__init__.py",
        "provider/zerodha/adapter.py",
        "provider/zerodha/auth.py",
        "provider/zerodha/credentials.py",
        "provider/zerodha/fetch.py",
        "provider/zerodha/instruments.py",
        "downloader/__init__.py",
        "downloader/queue.py",
        "downloader/sweep.py",
        "downloader/engine.py",
        "events/__init__.py",
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
        "README.md",
        "manifest.py",
        "runner.py",
        "worker.py",
        "execution.py",
        "models/__init__.py",
        "events/__init__.py",
        "engine/__init__.py",
        "tests/__init__.py",
    ],
    "risk": [
        "__init__.py",
        "README.md",
        "manifest.py",
        "models.py",
        "engine.py",
        "kill_switch.py",
        "session.py",
        "tests/__init__.py",
    ],
    "execution": [
        "__init__.py",
        "README.md",
        "manifest.py",
        "models/__init__.py",
        "events/__init__.py",
        "market_data/__init__.py",
        "market_data/provider.py",
        "market_data/replay.py",
        "market_data/normalizer.py",
        "runtime/__init__.py",
        "runtime/inspector.py",
        "runtime/lifecycle.py",
        "runtime/strategy_runtime.py",
        "runtime/session.py",
        "planner.py",
        "engine.py",
        "modes.py",
        "broker/__init__.py",
        "broker/adapter.py",
        "broker/paper.py",
        "broker/factory.py",
        "broker/sandbox.py",
        "broker/credentials.py",
        "broker/gates.py",
        "broker/activation.py",
        "portfolio/__init__.py",
        "portfolio/ledger.py",
        "portfolio/reconcile.py",
        "journal.py",
        "replay.py",
        "regime.py",
        "adaptive/__init__.py",
        "adaptive/attention.py",
        "adaptive/memory.py",
        "adaptive/confidence.py",
        "ml_interfaces.py",
        "tests/__init__.py",
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
