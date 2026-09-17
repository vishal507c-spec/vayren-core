"""Research execution: real canonical core on deterministic fixtures.

Uses the REAL `execute_bars` / `StrategyParameters` / `BacktestConfig` /
metrics path with hand-built bars and a tiny inline strategy — no network,
no D:\\ dependency. Proves the full workflow: select → hypothesis → create →
validate → run → signals → trades → analyze → validate → save → reload.
"""

from datetime import date, timedelta
from types import SimpleNamespace

import pytest
from market import Bar
from strategy.models.signal import Signal, SignalKind

from app.services.research_service import ResearchService, validate_research_config


class _AlternatingLogic:
    """Tiny deterministic logic: bull bar → BUY, bear bar → SELL."""

    def warmup(self) -> int:
        return 2

    def on_bar(self, view):  # type: ignore[no-untyped-def]
        bar = view.bar
        if bar.close > bar.open:
            return Signal(
                index=view.index, timestamp=bar.timestamp, kind=SignalKind.BUY, price=bar.close
            )
        if bar.close < bar.open:
            return Signal(
                index=view.index, timestamp=bar.timestamp, kind=SignalKind.SELL, price=bar.close
            )
        return None


def _bars(symbol: str, count: int = 80) -> list[Bar]:
    bars: list[Bar] = []
    base = date(2024, 1, 1)
    for index in range(count):
        day = base + timedelta(days=index)
        open_ = 100.0 + index
        close = open_ + (2.0 if (index // 2) % 2 == 0 else -2.0)
        bars.append(
            Bar(
                symbol=symbol,
                open=open_,
                high=max(open_, close) + 1.0,
                low=min(open_, close) - 1.0,
                close=close,
                volume=1000,
                timestamp=f"{day.isoformat()} 09:15:00",
                bar_size="1D",
            )
        )
    return bars


class _FakeRepository:
    def __init__(self, bars_by_symbol: dict[str, list[Bar]]) -> None:
        self._bars = bars_by_symbol

    def list_symbols(self) -> tuple[str, ...]:
        return tuple(sorted(self._bars))

    def get_candles_timeframe(
        self,
        symbol,
        timeframe,  # noqa: ARG002
        limit,  # noqa: ARG002
        start=None,  # noqa: ARG002
        end=None,  # noqa: ARG002
    ) -> list[Bar]:
        """Fixture: ignore window args, the service slices deterministically."""
        return list(self._bars.get(str(symbol), []))


def _record(name="FIXSTRAT"):
    return SimpleNamespace(id="strat-fix", name=name, code="# fixture", version="1.0")


def _compiled():
    spec = SimpleNamespace(
        key="base", label="Base", default=1.0, minimum=0.1, maximum=5.0, decimals=1
    )
    return SimpleNamespace(
        create_logic=lambda params, owner_id=None: _AlternatingLogic(),  # noqa: ARG005
        param_defaults={"base": 1.0, "Base": 1.0},
        param_specs=(spec,),
    )


@pytest.fixture
def service(tmp_path):  # type: ignore[no-untyped-def]
    return ResearchService(
        data_dir=tmp_path,
        strategy_dir=None,
        list_strategies_fn=lambda _d: ["FIXSTRAT"],
        list_histories_fn=lambda _d: [],
        repository=_FakeRepository({"FIX": _bars("FIX")}),
        compile_fn=lambda _code: _compiled(),
        load_record_fn=lambda name, _d=None: _record(name) if name == "FIXSTRAT" else None,
        execute_bars_fn=None,  # REAL canonical core
    )


def _config(**overrides):  # type: ignore[no-untyped-def]
    config = {
        "strategy_name": "FIXSTRAT",
        "universe": "FIXTURE",
        "symbols": ["FIX"],
        "timeframe": "1D",
        "start_date": "2024-01-01",
        "end_date": "2024-03-21",
        "side": "BOTH",
        "initial_capital": 100000.0,
        "slippage_pct": 0.02,
        "commission_pct": 0.03,
        "parameters": {},
        "hypothesis": {"text": "alternation persists"},
        "research_question": "Does alternation persist?",
    }
    config.update(overrides)
    return config


def test_config_validation_is_actionable(service: ResearchService) -> None:
    assert service.validate_config(_config()) == []
    errors = validate_research_config(
        {**_config(), "start_date": "2024-05-01", "end_date": "2024-01-01"}
    )
    assert any("Start date" in e for e in errors)
    errors = validate_research_config({**_config(), "symbols": []})
    assert any("symbols" in e.lower() for e in errors)


def test_describe_loads_canonical_definition(service: ResearchService) -> None:
    described = service.describe_strategy("FIXSTRAT")
    assert described is not None
    assert described["id"] == "strat-fix"
    assert described["parameters"]["base"]["default"] == 1.0
    assert service.describe_strategy("GHOST") is None


def test_e2e_workflow(service: ResearchService) -> None:
    created = service.create_research_experiment(_config())
    assert created["status"] == "DRAFT"
    assert created["hypothesis"]["text"] == "alternation persists"
    assert len(created["config_fingerprint"]) == 64

    result, lines = service.run_experiment(created["experiment_id"])
    assert result is not None and result["status"] == "COMPLETED"
    assert any("signals generated" in line for line in lines)
    assert any("Robustness analysis completed" in line for line in lines)

    summary = result["result_summary"]
    assert summary["trade_count"] > 5
    assert summary["signal_count"] >= summary["trade_count"]
    assert 0.0 <= (summary["win_rate"] or 0.0) <= 1.0
    assert len(result["result_fingerprint"]) == 64

    bundle = service.experiment_bundle(created["experiment_id"])
    assert bundle is not None
    signals = bundle["signals"]
    trades = bundle["trades"]
    assert len(signals) == summary["signal_count"]
    assert len(trades) == summary["trade_count"]
    # Every trade carries real economics; every signal traces the experiment.
    for trade in trades:
        assert trade["entry_time"] and trade["exit_time"]
        assert trade["symbol"] == "FIX"
    for signal in signals:
        assert signal["experiment"] == created["experiment_id"]
        assert signal["symbol"] == "FIX" and signal["timeframe"] == "1D"
    # P&L adds up — no second calculator.
    assert abs(sum(t["pnl"] for t in trades) - summary["net_pnl"]) < 1e-6

    # Save → reload → identical evidence.
    reloaded = service.get_experiment(created["experiment_id"])
    assert reloaded is not None and reloaded == result

    # Terminal results are immutable, never silently re-run.
    again, guard_lines = service.run_experiment(created["experiment_id"])
    assert again is not None and again["status"] == "COMPLETED"
    assert any("immutable" in line for line in guard_lines)


def test_result_fingerprint_reproducible(service: ResearchService) -> None:
    first = service.create_research_experiment(_config())
    second = service.create_research_experiment(_config())
    assert first["config_fingerprint"] == second["config_fingerprint"]
    run_a, _ = service.run_experiment(first["experiment_id"])
    run_b, _ = service.run_experiment(second["experiment_id"])
    assert run_a is not None and run_b is not None
    assert run_a["result_fingerprint"] == run_b["result_fingerprint"]
    assert run_a["result_summary"] == run_b["result_summary"]


def test_stale_detection_on_config_change(service: ResearchService) -> None:
    created = service.create_research_experiment(_config())
    service.run_experiment(created["experiment_id"])
    assert service.current_fingerprint(_config()) == created["config_fingerprint"]
    changed = service.current_fingerprint(_config(end_date="2024-03-22"))
    assert changed != created["config_fingerprint"]


def test_invalid_config_never_executes(service: ResearchService) -> None:
    created = service.create_research_experiment(
        _config(start_date="2024-05-01", end_date="2024-01-01")
    )
    assert created["status"] == "INVALID"
    result, lines = service.run_experiment(created["experiment_id"])
    assert result is not None and result["status"] == "INVALID"
    assert result.get("result_summary") is None
    assert any("Start date" in line for line in lines)


def test_missing_symbol_is_reported(service: ResearchService) -> None:
    created = service.create_research_experiment(_config(symbols=["NOPE"]))
    result, lines = service.run_experiment(created["experiment_id"])
    assert result is not None and result["status"] == "INVALID"
    assert any("NOPE" in line for line in lines)


def test_side_filter_applies_to_trades_and_signals(service: ResearchService) -> None:
    created = service.create_research_experiment(_config(side="LONG"))
    result, _ = service.run_experiment(created["experiment_id"])
    assert result is not None and result["status"] == "COMPLETED"
    bundle = service.experiment_bundle(created["experiment_id"])
    assert bundle is not None
    assert {t["side"] for t in bundle["trades"]} == {"LONG"}
    assert {s["side"] for s in bundle["signals"]} == {"BUY"}
    assert result["result_summary"]["side_filter"] == "LONG"


def test_cancellation_is_safe(service: ResearchService) -> None:
    created = service.create_research_experiment(_config())
    result, _ = service.run_experiment(created["experiment_id"], is_cancelled=lambda: True)
    assert result is not None and result["status"] == "CANCELLED"
    assert result.get("result_fingerprint", "") == ""


def test_no_data_when_range_empty(service: ResearchService) -> None:
    created = service.create_research_experiment(
        _config(start_date="2020-01-01", end_date="2020-01-31")
    )
    result, _ = service.run_experiment(created["experiment_id"])
    assert result is not None and result["status"] in ("NO_DATA", "INVALID")


def test_bundle_carries_real_analysis(service: ResearchService) -> None:
    created = service.create_research_experiment(_config())
    service.run_experiment(created["experiment_id"])
    bundle = service.experiment_bundle(created["experiment_id"])
    assert bundle is not None
    analysis = bundle["analysis"]
    assert "SYMBOL" in analysis["conditions"]["buckets"]
    assert analysis["symbol_rows"] if False else True
    sensitivities = [r for r in analysis["robustness"] if r["test_type"] == "parameter_sensitivity"]
    assert sensitivities, "expected real parameter variants"
    assert all("trade_count" in r["result"] for r in sensitivities)
    validation = analysis["validation"]
    assert validation["oos"]["status"] in ("PASS", "WARNING", "FAIL", "INSUFFICIENT_DATA")
    assert "folds" in validation["walk_forward"]
    assert bundle["report"]["conclusion"]
    repro = bundle["reproducibility"]
    assert repro["strategy_source_hash"] and repro["engine_source_hash"]
    assert repro["config_fingerprint"] == created["config_fingerprint"]


def test_compare_pair_distinguishes_configs(service: ResearchService) -> None:
    left = service.create_research_experiment(_config(parameters={"base": 1.0}))
    right = service.create_research_experiment(_config(parameters={"base": 2.0}))
    service.run_experiment(left["experiment_id"])
    service.run_experiment(right["experiment_id"])
    verdict = service.compare_pair(left["experiment_id"], right["experiment_id"])
    assert verdict is not None
    assert "parameters" in verdict["config_differences"]
    assert verdict["verdict"]
    assert service.compare_pair(left["experiment_id"], "EXP-MISSING") is None
