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
