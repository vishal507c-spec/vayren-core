"""Research workspace workflow: create → run state → completed bundle → STALE."""

from types import SimpleNamespace

from app.ui.research_workspace import ResearchWorkspace


def _bundle(exp_id="EXP-T1", fingerprint="fp-live"):
    summary = {
        "trade_count": 4,
        "signal_count": 6,
        "net_pnl": 120.5,
        "win_rate": 0.75,
        "profit_factor": 2.0,
        "expectancy": 30.125,
        "avg_win": 50.0,
        "avg_loss": -20.0,
        "max_drawdown_pct": 5.0,
        "max_drawdown_abs": 200.0,
        "sharpe": 1.1,
        "sortino": 1.4,
        "payoff_ratio": 2.5,
    }
    return {
        "experiment": {
            "experiment_id": exp_id,
            "strategy_id": "OBR",
            "strategy_version": "v1",
            "execution_ids": [f"EXP-{exp_id}"],
            "hypothesis": {"text": "edge persists"},
            "configuration": {"universe": "NIFTY 500"},
            "status": "COMPLETED",
            "symbols": ["AAA", "BBB"],
            "timeframe": "5m",
            "start_date": "2023-01-01",
            "end_date": "2023-03-31",
            "parameters": {"p": 1.0},
            "created_at": "2024-01-01T00:00:00",
            "config_fingerprint": fingerprint,
            "result_fingerprint": "r" * 64,
            "executed_at": "2024-01-02T00:00:00",
            "validation_status": "WARNING",
            "reproducibility": {
                "experiment_id": exp_id,
                "strategy_version": "v1",
                "strategy_id": "OBR",
                "dataset_version": "dv1",
                "parameters": {"p": 1.0},
                "timeframe": "5m",
                "date_range": "2023-01-01 → 2023-03-31",
            },
            "report": {"conclusion": "Evidence supports an in-sample edge."},
        },
        "signals": [
            {
                "time": "2023-01-02 09:15:00",
                "symbol": "AAA",
                "timeframe": "5m",
                "side": "BUY",
                "price": 100.0,
                "event": "BUY",
                "strategy": "OBR",
                "experiment": exp_id,
            },
        ],
        "trades": [
            {
                "entry_time": "2023-01-02 09:15:00",
                "exit_time": "2023-01-02 10:15:00",
                "side": "LONG",
                "quantity": 10.0,
                "pnl": 120.5,
                "exit_reason": "SIGNAL",
                "bars_held": 12,
                "entry_price": 100.0,
                "exit_price": 101.0,
            },
        ],
        "summary": summary,
        "analysis": {
            "robustness": [
                {
                    "test_type": "parameter_sensitivity",
                    "input": {"param": "p", "value": 1.0},
                    "stability": "PASS",
                    "evidence": {"variant_expectancy": 30.0},
                }
            ],
            "validation": {"status": "WARNING", "summary": "Marginal"},
        },
    }


def _stub(fingerprint="fp-live", store=None):
    saved: dict = {} if store is None else store
    service = SimpleNamespace(
        refresh_datasets=lambda: [],
        experiments=lambda: list(saved.values()),
        available_strategies=lambda: ["OBR"],
        describe_strategy=lambda _n: {"parameters": {}, "version": "v1"},
        validate_config=lambda _c: [],
        create_research_experiment=lambda cfg: saved.setdefault(
            "EXP-T1",
            {
                "experiment_id": "EXP-T1",
                "status": "DRAFT",
                "hypothesis": cfg.get("hypothesis", {}),
                "configuration": {},
                "reproducibility": {},
            },
        ),
        run_experiment=lambda _e, **_k: (None, []),
        experiment_bundle=lambda exp_id: _bundle(exp_id) if exp_id in saved else None,
        current_fingerprint=lambda _c: fingerprint,
        compare_pair=lambda _a, _b: None,
        get_dataset=lambda _s: None,
        run_analysis=lambda _s: SimpleNamespace(
            status="NO DATA", metrics={}, notes=("No dataset",), trades=(), signals=()
        ),
        run_robustness=lambda _s: [],
        run_validation=lambda _s: {"status": "NOT RUN", "summary": "Not run."},
        comparison=lambda: [],
        data_quality=lambda _s: {},
        reproducibility=lambda _s, _e=None: {},
        list_trades=lambda _s: (),
        list_signals=lambda _s: (),
    )
    return service


def test_config_form_collects_workflow_inputs(qt_app) -> None:
    assert qt_app is not None
    workspace = ResearchWorkspace()
    workspace.set_service(_stub())
    workspace._strategy_combo.setCurrentText("OBR")
    workspace._symbols_edit.setText("RELIANCE, TCS")
    workspace._hypothesis_edit.setPlainText("opening momentum persists")
    config = workspace._collect_config()
    assert config["strategy_name"] == "OBR"
    assert config["symbols"] == ["RELIANCE", "TCS"]
    assert config["timeframe"] == "15m"
    assert config["hypothesis"] == {"text": "opening momentum persists"}


def test_create_requires_hypothesis(qt_app) -> None:
    assert qt_app is not None
    workspace = ResearchWorkspace()
    workspace.set_service(_stub())
    workspace._hypothesis_edit.setPlainText("")
    workspace._on_create_clicked()
    assert "hypothesis" in workspace._log_label.text().lower()


def test_create_then_render_completed_bundle(qt_app) -> None:
    assert qt_app is not None
    workspace = ResearchWorkspace()
    workspace.set_service(_stub())
    workspace._strategy_combo.setCurrentText("OBR")
    workspace._hypothesis_edit.setPlainText("edge persists")
    created: list[str] = []
    workspace.experiment_created.connect(created.append)
    workspace._on_create_clicked()
    assert created == ["EXP-T1"]
    # Show the stored COMPLETED experiment: real engine values reach the UI.
    workspace._show_experiment(
        {
            "experiment_id": "EXP-T1",
            "strategy_id": "OBR",
            "version_id": "v1",
            "execution_ids": [],
            "hypothesis": {"text": "edge persists"},
            "configuration": {},
            "result": None,
            "created_at": "",
        }
    )
    assert workspace._metrics_block.value("trades").text() == "4"
    assert workspace._signals_table.rowCount() == 1
    assert workspace._trades_table.rowCount() == 1
    assert workspace._robustness_table.rowCount() == 1
    assert workspace._status_badge.text() == "COMPLETED"
    assert workspace._validation_block.value("status").text() == "WARNING"
    assert "in-sample edge" in workspace._notes_label.text()
    assert "EXP-T1" in workspace._log_view.toPlainText()


def test_stale_badge_when_form_changes(qt_app) -> None:
    assert qt_app is not None
    workspace = ResearchWorkspace()
    workspace.set_service(_stub(fingerprint="fp-other"))
    workspace._strategy_combo.setCurrentText("OBR")
    workspace._hypothesis_edit.setPlainText("edge persists")
    workspace._on_create_clicked()
    workspace._show_experiment(
        {
            "experiment_id": "EXP-T1",
            "strategy_id": "OBR",
            "version_id": "v1",
            "execution_ids": [],
            "hypothesis": {"text": "edge persists"},
            "configuration": {},
            "result": None,
            "created_at": "",
        }
    )
    # Bundle was fingerprinted fp-live, live form differs → STALE, never COMPLETE.
    assert workspace._status_badge.text() == "STALE"
    assert "STALE" in workspace._notes_label.text()


def test_draft_bundle_is_truthful(qt_app) -> None:
    assert qt_app is not None
    service = _stub()
    workspace = ResearchWorkspace()
    workspace.set_service(service)
    workspace._render_experiment_bundle(
        {
            "experiment": {
                "experiment_id": "EXP-D",
                "strategy_id": "OBR",
                "strategy_version": "v1",
                "execution_ids": [],
                "hypothesis": {"text": "h"},
                "configuration": {},
                "status": "DRAFT",
                "symbols": [],
                "timeframe": "5m",
                "start_date": "",
                "end_date": "",
                "parameters": {},
                "created_at": "",
                "reproducibility": {},
            },
            "signals": [],
            "trades": [],
            "summary": {},
            "analysis": {},
        }
    )
    assert workspace._status_badge.text() == "DRAFT"
    assert "not executed" in workspace._notes_label.text().lower()
    assert workspace._signals_table.rowCount() == 0
