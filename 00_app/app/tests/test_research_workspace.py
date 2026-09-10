"""Research service + workspace: real engine, honest empty states."""

from types import SimpleNamespace

import pytest

from app.services.research_service import ResearchService
from app.ui.research_workspace import ResearchWorkspace


def _history(strategy_id: str, execution_id: str, pnls: list[float]):
    trades = [SimpleNamespace(pnl=pnl) for pnl in pnls]
    snapshot = SimpleNamespace(
        strategy_id=strategy_id,
        version_id="v1",
        execution_id=execution_id,
        data_identity={"symbol": "TEST", "timeframe": "15m"},
        parameters={"fast": 2},
    )
    return SimpleNamespace(snapshot=snapshot, trades=trades, signals=[])


@pytest.fixture
def service() -> ResearchService:
    histories = [
        _history("sma", "exec-1", [10.0, -5.0, 8.0]),
        _history("sma", "exec-2", [4.0, 4.0]),
    ]
    return ResearchService(
        data_dir=None,
        strategy_dir=None,
        list_strategies_fn=lambda _d: ["sma"],
        list_histories_fn=lambda _d: histories,
    )


def test_datasets_built_from_histories(service: ResearchService) -> None:
    summaries = service.refresh_datasets()
    assert len(summaries) == 1
    summary = summaries[0]
    assert summary.strategy_id == "sma"
    assert summary.execution_ids == ("exec-1", "exec-2")
    assert summary.trade_count == 5
    assert summary.data_identity["symbol"] == "TEST"


def test_analysis_uses_real_engine(service: ResearchService) -> None:
    service.refresh_datasets()
    view = service.run_analysis("sma")
    assert view.metrics["trades"] == 5
    assert view.metrics["win_rate"] != "N/A"
    assert view.metrics["net_profit"] != "N/A"
    assert isinstance(view.notes, tuple)


def test_analysis_unknown_strategy_is_honest(service: ResearchService) -> None:
    service.refresh_datasets()
    view = service.run_analysis("ghost")
    assert "No dataset" in view.notes[0]


def test_experiment_roundtrip_tmp_data_dir(tmp_path) -> None:
    svc = ResearchService(
        data_dir=tmp_path,
        strategy_dir=None,
        list_strategies_fn=lambda _d: [],
        list_histories_fn=lambda _d: [],
    )
    saved = svc.create_experiment("sma", "v1", ["exec-1"], "fast beats slow", {"k": 1})
    assert saved["experiment_id"].startswith("EXP-")
    experiments = svc.experiments()
    assert len(experiments) == 1
    assert experiments[0]["hypothesis"]["text"] == "fast beats slow"


def test_workspace_renders_service_state(qt_app, service: ResearchService) -> None:
    assert qt_app is not None
    workspace = ResearchWorkspace()
    workspace.set_service(service)
    assert workspace._dataset_combo.count() == 1
    assert "TEST" in workspace._identity_label.text()
    workspace._on_run_clicked()
    assert workspace._metrics_block.value("trades").text() == "5"
    assert "EXP-" not in workspace._log_label.text()


def test_workspace_empty_without_service(qt_app) -> None:
    assert qt_app is not None
    workspace = ResearchWorkspace()
    workspace.set_service(None)
    workspace.refresh()
    assert workspace._dataset_combo.count() == 0
    assert "No research service" in workspace._identity_label.text()


def test_workspace_save_requires_hypothesis(qt_app, tmp_path) -> None:
    assert qt_app is not None
    saving = ResearchService(
        data_dir=tmp_path,
        strategy_dir=None,
        list_strategies_fn=lambda _d: ["sma"],
        list_histories_fn=lambda _d: [_history("sma", "exec-1", [1.0])],
    )
    workspace = ResearchWorkspace()
    workspace.set_service(saving)
    workspace._on_save_clicked()
    assert "hypothesis" in workspace._log_label.text().lower()
    workspace._hypothesis_edit.setPlainText("mean reversion works here")
    created: list[str] = []
    workspace.experiment_created.connect(created.append)
    workspace._on_save_clicked()
    assert len(created) == 1
    assert saving.experiments()[0]["experiment_id"] == created[0]


def test_institutional_metrics_honest_na(service: ResearchService) -> None:
    service.refresh_datasets()
    view = service.run_analysis("sma")
    assert view.status == "COMPLETE"
    # Real engine values present …
    assert view.metrics["trades"] == 5
    assert view.metrics["sortino"] != "N/A"
    assert view.metrics["payoff_ratio"] != "N/A"
    # … structural gaps stay N/A, never zero-filled.
    assert view.metrics["cagr"] == "N/A"
    assert view.metrics["volatility"] == "N/A"
    assert view.metrics["exposure"] == "N/A"
    assert view.metrics["turnover"] == "N/A"


def test_analysis_status_no_trades_is_honest() -> None:
    empty = ResearchService(
        data_dir=None,
        strategy_dir=None,
        list_strategies_fn=lambda _d: ["flat"],
        list_histories_fn=lambda _d: [_history("flat", "exec-1", [])],
    )
    empty.refresh_datasets()
    view = empty.run_analysis("flat")
    assert view.status in ("NO DATA", "NO TRADES")
    assert view.metrics["win_rate"] == "N/A"


def test_data_quality_never_fabricates(service: ResearchService) -> None:
    service.refresh_datasets()
    quality = service.data_quality("sma")
    assert quality["symbols"] == "TEST"
    assert quality["missing_bars"] == "NOT CHECKED"
    assert quality["duplicate_bars"] == "NOT CHECKED"
    assert quality["bars"] == "N/A"
    ghost = service.data_quality("ghost")
    assert ghost["data_status"] == "NO DATA"


def test_reproducibility_traces_origin(service: ResearchService) -> None:
    service.refresh_datasets()
    repro = service.reproducibility("sma", "EXP-000124")
    assert repro["experiment_id"] == "EXP-000124"
    assert repro["dataset"] == "sma"
    assert repro["timeframe"] == "15m"


def test_robustness_and_validation_use_real_engine(service: ResearchService) -> None:
    service.refresh_datasets()
    robustness = service.run_robustness("sma")
    assert isinstance(robustness, list)
    verdict = service.run_validation("sma")
    assert verdict["status"] in ("PASS", "WARNING", "FAIL", "NOT RUN", "FAILED")
    assert "summary" in verdict
    ghost_verdict = service.run_validation("ghost")
    assert ghost_verdict["status"] == "NOT RUN"


def test_comparison_is_data_driven(tmp_path) -> None:
    svc = ResearchService(
        data_dir=tmp_path,
        strategy_dir=None,
        list_strategies_fn=lambda _d: [],
        list_histories_fn=lambda _d: [],
    )
    assert svc.comparison() == []
    svc.create_experiment("sma", "v1", ["exec-1"], "edge persists", {})
    rows = svc.comparison()
    assert len(rows) == 1
    assert rows[0]["status"] == "NO RESULT"


def test_workspace_institutional_layout(qt_app, service: ResearchService) -> None:
    assert qt_app is not None
    workspace = ResearchWorkspace()
    workspace.set_service(service)
    workspace._on_run_clicked()
    # 8-column signal inspector, sortable + filterable.
    assert workspace._signals_table.columnCount() == 8
    assert workspace._trades_table.columnCount() == 8
    assert workspace._status_badge.text() == "COMPLETE"
    # Unsupported metrics render N/A, never fake numbers.
    assert workspace._metrics_block.value("cagr").text() == "N/A"
    assert workspace._metrics_block.value("exposure").text() == "N/A"
    # Trade inspector exposes real entry/exit economics.
    assert workspace._trades_table.rowCount() == 5
    # Data quality + reproducibility are honest.
    assert workspace._quality_block.value("missing_bars").text() == "NOT CHECKED"
    assert workspace._repro_block.value("dataset").text() == "sma"
    # Log records only real session actions.
    assert "Analysis complete" in workspace._log_view.toPlainText()
    assert "EXP-" not in workspace._log_view.toPlainText()


def test_workspace_empty_state_is_ready(qt_app) -> None:
    assert qt_app is not None
    workspace = ResearchWorkspace()
    workspace.set_service(None)
    workspace.refresh()
    assert workspace._status_badge.text() == "READY"
    assert workspace._signals_table.columnCount() == 8
    assert workspace._comparison_table.rowCount() == 0
    assert "No research service" in workspace._identity_label.text()
