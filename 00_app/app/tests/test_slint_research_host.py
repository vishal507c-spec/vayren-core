"""Slint Research host: bridge contract + viewport + intent dispatch.

The host draws nothing (pixels come from Rust) and computes nothing
(results come from the Python ResearchService engine); these tests pin the
bridge schema mapping, fail-closed library loading, real offscreen native
rendering, and that every UI intent lands on the REAL service exactly once.
"""

from types import SimpleNamespace

import pytest

from app.services.slint_research_host import (
    NativeViewError,
    SlintResearchHost,
    find_view_library,
    load_view_library,
    parse_params,
    research_snapshot_dict,
    service_config_from_payload,
)


class FakeService:
    """Deterministic stand-in with the same public surface as ResearchService."""

    def __init__(self) -> None:
        self.created: list[dict] = []
        self.exps: list[dict] = []
        self.bundles: dict[str, dict] = {}
        self.completed = {
            "experiment_id": "EXP-ONE",
            "strategy_id": "OBR",
            "status": "COMPLETED",
            "universe": "NIFTY 500",
            "symbols": ["RELIANCE", "TCS"],
            "timeframe": "5m",
            "start_date": "2024-01-01",
            "end_date": "2024-03-31",
            "side": "BOTH",
            "initial_capital": 1000000.0,
            "slippage_pct": 0.02,
            "commission_pct": 0.03,
            "parameters": {"refIndex": 3.0},
            "hypothesis": {"text": "opening momentum persists"},
            "configuration": {"research_question": "why?", "expected_effect": "+"},
            "config_fingerprint": "cfg123",
            "result_fingerprint": "res123",
            "result_summary": {
                "trade_count": 2,
                "signal_count": 3,
                "net_pnl": 120.5,
                "win_rate": 0.5,
                "profit_factor": 2.0,
                "max_drawdown_pct": 1.5,
                "sharpe": 0.8,
            },
            "report": {"conclusion": "Evidence supports an in-sample edge."},
            "analysis": {},
        }

    def available_strategies(self):
        return ["OBR"]

    def describe_strategy(self, name):
        return {"name": name, "version": "1.0"}

    def experiments(self):
        return list(self.exps)

    def create_research_experiment(self, config):
        self.created.append(config)
        return {"experiment_id": "EXP-NEW", "status": "DRAFT"}

    def get_experiment(self, exp_id):
        for exp in self.exps:
            if exp.get("experiment_id") == exp_id:
                return dict(exp)
        return None

    def experiment_bundle(self, exp_id):
        return self.bundles.get(exp_id)


@pytest.fixture
def service() -> FakeService:
    svc = FakeService()
    svc.exps.append(svc.completed)
    svc.bundles["EXP-ONE"] = {
        "experiment": svc.completed,
        "signals": [
            {
                "time": "2024-01-02 09:15:00",
                "symbol": "RELIANCE",
                "timeframe": "5m",
                "side": "BUY",
                "price": 2451.1,
                "event": "BUY",
                "strategy": "OBR",
                "experiment": "EXP-ONE",
            }
        ],
        "trades": [],
    }
    return svc


# ── pure bridge mapping ─────────────────────────────────────────────


def test_snapshot_maps_real_service_facts(service: FakeService) -> None:
    snap = research_snapshot_dict(service, "EXP-ONE", ["log line"])
    assert snap["strategies"] == [{"name": "OBR", "description": "", "version": "1.0"}]
    assert snap["experiments"][0] == {"id": "EXP-ONE", "strategy": "OBR", "status": "COMPLETED"}
    assert snap["selected_id"] == "EXP-ONE"
    assert snap["status"] == "COMPLETED"
    assert snap["log"] == ["log line"]
    defaults = snap["defaults"]
    assert defaults["symbols"] == "RELIANCE, TCS"
    assert defaults["capital"] == "1000000"
    assert defaults["hypothesis"] == "opening momentum persists"
    assert defaults["parameters"] == "refIndex=3.0"
    bundle = snap["bundle"]
    assert bundle["experiment"]["result_summary"]["trade_count"] == 2
    assert len(bundle["signals"]) == 1


def test_snapshot_without_selection_is_honest(service: FakeService) -> None:
    snap = research_snapshot_dict(service, "", [])
    # No explicit id: defaults to the newest experiment (never invents one).
    assert snap["selected_id"] == "EXP-ONE"
    empty = research_snapshot_dict(FakeService(), "gone", [])
    assert empty["experiments"] == [] and empty["bundle"] is None


def test_payload_parsing_roundtrips_the_form(service: FakeService) -> None:  # noqa: ARG001
    payload = {
        "strategy_name": "OBR",
        "universe": "NIFTY 500",
        "symbols": "reliance, tcs",
        "timeframe": "5m",
        "start_date": "2024-01-01",
        "end_date": "2024-03-31",
        "side": "long",
        "initial_capital": "1000000",
        "slippage_pct": "0.02",
        "commission_pct": "0.03",
        "parameters": "refIndex=3, bad",
        "hypothesis": "edge persists",
    }
    config = service_config_from_payload(payload)
    assert config["symbols"] == ["RELIANCE", "TCS"]
    assert config["side"] == "LONG"
    assert config["initial_capital"] == 1000000.0
    assert config["parameters"] == {"refIndex": 3.0}
    assert parse_params("") == {}


def test_missing_library_is_fail_closed(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("VAYREN_RESEARCH_VIEW_LIB", str(tmp_path / "absent.dll"))
    with pytest.raises(NativeViewError):
        find_view_library()
    with pytest.raises(NativeViewError):
        load_view_library()


# ── intent dispatch (service is the only execution authority) ───────


def test_create_intent_lands_on_the_service_once(  # noqa: ARG001 (qt_app = event-loop hook)
    qt_app,
    service: FakeService,  # noqa: ARG001
) -> None:
    assert qt_app is not None
    host = SlintResearchHost(service=service)
    host._dispatch(
        {
            "action": "create",
            "config": {
                "strategy_name": "OBR",
                "symbols": "RELIANCE",
                "timeframe": "5m",
                "start_date": "2024-01-01",
                "end_date": "2024-03-31",
                "side": "BOTH",
                "initial_capital": "1000000",
                "slippage_pct": "0.02",
                "commission_pct": "0.03",
                "parameters": "",
                "hypothesis": "edge persists",
            },
        }
    )
    assert len(service.created) == 1
    assert service.created[0]["initial_capital"] == 1000000.0
    assert host._selected_id == "EXP-NEW"


def test_create_without_hypothesis_calls_nothing(  # noqa: ARG001 (qt_app = event-loop hook)
    qt_app,
    service: FakeService,  # noqa: ARG001
) -> None:
    assert qt_app is not None
    host = SlintResearchHost(service=service)
    host._dispatch({"action": "create", "config": {"hypothesis": ""}})
    assert service.created == []


def test_run_reuses_draft_and_forks_completed(  # noqa: ARG001 (qt_app = event-loop hook)
    qt_app,
    service: FakeService,
    monkeypatch,  # noqa: ARG001
) -> None:
    assert qt_app is not None
    started: list[str] = []
    monkeypatch.setattr(
        SlintResearchHost, "_start_worker", lambda _self, _svc, e: started.append(e)
    )
    host = SlintResearchHost(service=service)
    draft = {"experiment_id": "EXP-D", "status": "DRAFT", "result_fingerprint": ""}
    service.exps.append(draft)
    host._selected_id = "EXP-D"
    host._dispatch({"action": "run", "config": {"hypothesis": "h"}})
    assert started == ["EXP-D"]  # the DRAFT is executed, not re-created
    started.clear()
    host._selected_id = "EXP-ONE"  # COMPLETED: immutable — fork a new experiment
    host._dispatch({"action": "run", "config": {"hypothesis": "h"}})
    assert len(service.created) == 1
    assert started == ["EXP-NEW"]


def test_select_and_cancel_forward(  # noqa: ARG001 (qt_app = event-loop hook)
    qt_app,
    service: FakeService,  # noqa: ARG001
) -> None:
    assert qt_app is not None
    host = SlintResearchHost(service=service)
    host._dispatch({"action": "select", "id": "EXP-ONE"})
    assert host._selected_id == "EXP-ONE"
    cancelled: list[bool] = []
    host._worker = SimpleNamespace(request_cancel=lambda: cancelled.append(True))
    host._dispatch({"action": "cancel", "id": "EXP-ONE"})
    assert cancelled == [True]


# ── real native rendering (offscreen, same pixels production gets) ──


def _needs_cdylib() -> pytest.MarkDecorator:
    try:
        find_view_library()
    except NativeViewError:
        return pytest.mark.skip(reason="native Research view cdylib not built")
    return pytest.mark.skipif(False, reason="")


@_needs_cdylib()
def test_host_renders_real_completed_experiment(qt_app, service: FakeService) -> None:
    """Engine facts → Rust → varied real pixels inside the Qt viewport."""
    assert qt_app is not None
    host = SlintResearchHost(service=service)
    try:
        assert host.is_native_available
        host.resize(1176, 657)
        host.show()
        assert host._ensure_view()
        host._push_snapshot(force=True)
        host._on_pump()
        frame = bytes(host._frame)
        assert len(frame) == 1176 * 657 * 3
        assert any(b != frame[0] for b in frame), "native frame must contain varied pixels"
        # Selection of the completed experiment pushes its bundle through
        # the C ABI without error (snapshot accepted = code 0).
        host._dispatch({"action": "select", "id": "EXP-ONE"})
        host._on_pump()
        assert host._last_pushed != ""
    finally:
        host.destroy_view()
        host.destroy_view()  # idempotent
        host.close()
