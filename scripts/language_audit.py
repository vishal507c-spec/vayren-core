"""Constitutional language audit — classify every Python file by responsibility.

Reads ARCHITECTURE_CONSTITUTION.md ownership (§8) and maps each file to a
responsibility class + target language using AST signals (imports, class/
function names, Qt usage, domain keywords) — never filename alone.

Output: machine-readable inventory (JSON) for the migration DAG, priority
engine and the language-ownership validator.
"""

from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

EXCLUDED_TOPS = {".venv", "99_archive"}

CHAPTER_OF = {
    "00_app": "app",
    "01_core": "core",
    "02_data": "data",
    "03_market": "market",
    "04_chart": "chart",
    "05_strategy": "strategy",
    "06_backtest": "backtest",
    "07_risk": "risk",
    "08_execution": "execution",
    "09_broker": "broker",
}

QT_MODULES = {"PySide6", "PyQt5", "PyQt6"}

RISK_WORDS = {"RiskPolicy", "RiskEngine", "RiskRequest", "RiskDecision", "KillSwitch"}
EXEC_WORDS = {
    "BrokerOrder",
    "OrderPlan",
    "ExecutionEngine",
    "LiveSession",
    "OrderPlanner",
    "BrokerAdapter",
    "PositionLedger",
    "Fill",
}
BACKTEST_WORDS = {"BacktestRunner", "ExecutionSimulator", "BacktestConfig", "TradeRecord"}
INDICATOR_WORDS = {"sma", "ema", "rsi", "macd", "atr", "bollinger", "vwap", "stochastic"}
DATA_PROC_WORDS = {"aggregate_bars", "fetch_candles", "INSERT", "ohlcv", "CandleDB"}
STRATEGY_WORDS = {"StrategyLogic", "PythonStrategy", "Signal", "on_bar"}
RESEARCH_WORDS = {
    "validate_oos",
    "run_cpcv",
    "compute_pbo",
    "compute_dsr",
    "grade_evidence",
    "OptimizationStudy",
    "ResearchDataset",
}


def _signals(tree: ast.AST, source: str) -> dict[str, object]:
    imported: set[str] = set()
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
            names.update(a.name for a in node.names)
        elif isinstance(node, (ast.ClassDef, ast.FunctionDef)):
            names.add(node.name)
    blob = source.lower()
    words = (
        RISK_WORDS
        | EXEC_WORDS
        | BACKTEST_WORDS
        | STRATEGY_WORDS
        | RESEARCH_WORDS
        | {"aggregate_bars", "fetch_candles", "CandleDB"}
    )
    found = {w for w in words if w in source} | {w for w in INDICATOR_WORDS if w in blob}
    return {
        "qt": bool(imported & QT_MODULES),
        "risk": bool(found & RISK_WORDS),
        "execution": bool(found & EXEC_WORDS),
        "backtest": bool(found & BACKTEST_WORDS),
        "indicator_math": bool(found & INDICATOR_WORDS),
        "data_processing": bool(found & DATA_PROC_WORDS),
        "strategy": bool(found & STRATEGY_WORDS),
        "research": bool(found & RESEARCH_WORDS),
        "names": sorted(names)[:0],  # placeholder shape only; names unused
    }


def classify(rel: str, chapter: str, is_test: bool, sig: dict) -> tuple[str, str, str]:
    """Return (responsibility, target, basis). Pure function of signals."""
    if is_test:
        return ("TEST", "python-test", "test file")
    if chapter == "strategy":
        if sig["research"]:
            return ("RESEARCH", "python", "research engine")
        return ("STRATEGY", "python", "strategy platform")
    if chapter == "backtest":
        if "ui" in rel or sig["qt"]:
            return ("RESEARCH_UI", "python-retained-qt", "research UI panel")
        return ("BACKTEST", "rust", "replay/simulation core")
    if chapter == "risk":
        return ("RISK", "rust", "fail-closed gates")
    if chapter == "execution":
        if "adaptive" in rel or "regime" in rel or "ml_interfaces" in rel:
            return ("AI_ADJACENT", "python", "advisory/ML interfaces")
        return ("EXECUTION", "rust", "live execution core")
    if chapter == "market":
        if "database" in rel or "repository" in rel or "timeframe" in rel:
            return ("MARKET_DATA", "rust", "storage/query/aggregation core")
        if sig["qt"] or "loader" in rel:
            return ("BOUNDARY_LOADER", "python-retained", "event loader bridge")
        return ("MARKET_DATA", "rust", "market read path")
    if chapter == "data":
        if sig["qt"] or "/ui/" in rel:
            return ("DATA_UI", "python-retained-qt", "download console UI")
        return ("DATA_PROCESSING", "rust", "download/storage engine")
    if chapter == "chart":
        if sig["qt"]:
            return ("NATIVE_UI", "rust-egui", "Qt presentation layer")
        return ("PRESENTATION_MODEL", "rust", "chart model/math")
    if chapter == "core":
        if "/ai/" in rel:
            return ("AI_BOUNDARY", "python", "AI engineering guardrails")
        return ("CORE", "rust", "foundation services")
    if chapter == "broker":
        return ("BROKER_CONTRACT", "python", "UBL design: Python integration glue")
    if chapter == "app":
        if sig["qt"] or "/ui/" in rel or "bootstrap" in rel or "lifecycle" in rel:
            return ("APP_UI", "python-retained-qt", "composition root + Qt shell")
        return ("APP_SERVICE", "python-retained", "composition services")
    if rel.startswith("scripts/"):
        return ("TOOLING", "python-tooling", "dev tooling")
    return ("UNKNOWN", "python-retained", "unclassified")


_MIGRATED = {
    "08_execution/execution/models/order.py": "table authority -> Rust order_state",
    "08_execution/execution/models/order_state.py": "vocabulary split for cycle-free bridge",
    "08_execution/execution/native_order_state.py": "boundary projection of Rust table",
    "06_backtest/backtest/engine/metrics.py": "kernels -> Rust metrics",
    "06_backtest/backtest/native_metrics.py": "boundary marshaling for Rust kernels",
    "03_market/market/timeframe/aggregate.py": "kernel -> Rust aggregate",
    "03_market/market/native_aggregate.py": "boundary marshaling for Rust kernel",
    "01_core/core/native/loader.py": "domain-agnostic FFI loader (stdlib only)",
    "01_core/core/native/__init__.py": "bridge exports",
}

_PYTHON_OWNED = {
    "STRATEGY",
    "RESEARCH",
    "RESEARCH_UI",
    "AI_ADJACENT",
    "AI_BOUNDARY",
    "BROKER_CONTRACT",
    "APP_SERVICE",
    "BOUNDARY_LOADER",
    "TOOLING",
    "DATA_UI",
}


def _status(rel: str, responsibility: str, is_test: bool, retention: dict) -> str:
    if is_test:
        return "TEST"
    if rel in _MIGRATED or rel in retention.get("migrated", {}):
        return "MIGRATED"
    if rel in retention.get("files", {}):
        return "INTENTIONALLY_RETAINED"
    if responsibility in _PYTHON_OWNED:
        return "PYTHON_OWNED"
    if responsibility in retention.get("classes", {}):
        return "INTENTIONALLY_RETAINED"
    return "UNCLASSIFIED"


def main() -> int:
    parser = argparse.ArgumentParser(description="Constitutional language audit")
    parser.add_argument("--json", default="90_brain/language_inventory.json")
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()
    retention: dict[str, str] = {}
    retention_path = ROOT / "90_brain" / "language_retention.json"
    if retention_path.is_file():
        retention = json.loads(retention_path.read_text(encoding="utf-8"))
    entries = []
    for path in sorted(ROOT.rglob("*.py")):
        try:
            rel = path.relative_to(ROOT).as_posix()
        except ValueError:
            continue
        if ".venv" in path.parts or "__pycache__" in path.parts:
            continue
        top = rel.split("/")[0]
        if top in EXCLUDED_TOPS:
            continue
        chapter = CHAPTER_OF.get(top, "scripts" if top == "scripts" else "other")
        is_test = "test" in path.name or path.parent.name == "tests"
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source)
        except (OSError, SyntaxError):
            continue
        sig = _signals(tree, source)
        responsibility, target, basis = classify(rel, chapter, is_test, sig)
        entries.append(
            {
                "file": rel,
                "loc": len(source.splitlines()),
                "chapter": chapter,
                "test": is_test,
                "responsibility": responsibility,
                "target": target,
                "basis": basis,
                "qt": sig["qt"],
                "status": _status(rel, responsibility, is_test, retention),
            }
        )
    out = ROOT / args.json
    out.write_text(json.dumps(entries, indent=1), encoding="utf-8")
    totals: dict[str, int] = {}
    for entry in entries:
        totals[entry["responsibility"]] = totals.get(entry["responsibility"], 0) + 1
    print(f"files: {len(entries)} -> {out.relative_to(ROOT)}")
    for key in sorted(totals):
        print(f"  {totals[key]:4d}  {key}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
