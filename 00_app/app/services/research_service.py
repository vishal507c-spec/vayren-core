"""ResearchService — composition service behind the Research workspace.

Wires the Python research engine (`strategy.research`) to datasets built
from persisted backtest execution histories. No computation lives in the
UI; this service owns dataset assembly, experiment persistence and
analysis runs. All external access goes through injected callables so the
workspace is testable without a strategy library on disk.

Research logic stays Python per the architecture constitution.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class DatasetSummary:
    """One analyzable dataset: a strategy version + its execution histories."""

    strategy_id: str
    version_id: str
    execution_ids: tuple[str, ...] = ()
    trade_count: int = 0
    signal_count: int = 0
    data_identity: dict[str, Any] = field(default_factory=dict)
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AnalysisView:
    """Flattened analysis result for display (all values backend-derived).

    `status` is one of READY / RUNNING / COMPLETE / FAILED / NO DATA /
    NO TRADES / INVALID EXPERIMENT. Metrics carry real engine values;
    anything the engine cannot compute is the string "N/A".
    """

    strategy_id: str
    version_id: str
    metrics: dict[str, Any] = field(default_factory=dict)
    notes: tuple[str, ...] = ()
    status: str = "READY"
    trades: tuple[Any, ...] = ()
    signals: tuple[Any, ...] = ()
    data_quality: dict[str, Any] = field(default_factory=dict)
    reproducibility: dict[str, Any] = field(default_factory=dict)


def _metric(value: Any) -> Any:
    if value is None:
        return "N/A"
    if isinstance(value, bool):
        return "YES" if value else "NO"
    if isinstance(value, float):
        return f"{value:,.4f}"
    return value


class ResearchService:
    """Dataset assembly + experiment store + analysis runs."""

    def __init__(
        self,
        data_dir: str | Path | None = None,
        strategy_dir: str | Path | None = None,
        list_strategies_fn: Callable[[Any], list[str]] | None = None,
        list_histories_fn: Callable[[Any], list[Any]] | None = None,
    ) -> None:
        self._data_dir = data_dir
        self._strategy_dir = strategy_dir
        self._list_strategies = list_strategies_fn
        self._list_histories = list_histories_fn
        self._datasets: dict[str, Any] = {}

    # ── datasets ──────────────────────────────────────────────

    def _strategy_names(self) -> list[str]:
        if self._list_strategies is None:
            return []
        try:
            return [str(name) for name in self._list_strategies(self._strategy_dir)]
        except Exception:
            return []

    def _histories(self) -> list[Any]:
        if self._list_histories is None:
            return []
        try:
            return list(self._list_histories(self._data_dir))
        except Exception:
            return []

    def refresh_datasets(self) -> list[DatasetSummary]:
        """Rebuild dataset summaries from histories. Never raises."""
        from strategy.research.dataset import ResearchDataset

        summaries: list[DatasetSummary] = []
        self._datasets = {}
        try:
            histories = self._histories()
        except Exception:
            histories = []
        by_strategy: dict[str, list[Any]] = {}
        for history in histories:
            snapshot = getattr(history, "snapshot", None)
            sid = str(getattr(snapshot, "strategy_id", "") or "")
            if sid:
                by_strategy.setdefault(sid, []).append(history)
        names = self._strategy_names() or sorted(by_strategy)
        for name in names:
            sid = str(name)
            group = by_strategy.get(sid, [])
            version = ""
            if group:
                first_snapshot = getattr(group[0], "snapshot", None)
                version = str(getattr(first_snapshot, "version_id", "") or "")
            try:
                dataset = ResearchDataset.from_histories(sid, version, group, None)
            except Exception:
                continue
            self._datasets[sid] = dataset
            identity = dict(getattr(dataset, "data_identity", {}) or {})
            summaries.append(
                DatasetSummary(
                    strategy_id=sid,
                    version_id=version,
                    execution_ids=tuple(getattr(dataset, "execution_ids", ()) or ()),
                    trade_count=len(getattr(dataset, "trades", ()) or ()),
                    signal_count=len(getattr(dataset, "signals", ()) or ()),
                    data_identity=identity,
                    parameters=dict(getattr(dataset, "parameters", {}) or {}),
                )
            )
        return summaries

    def get_dataset(self, strategy_id: str) -> Any | None:
        return self._datasets.get(str(strategy_id))

    # ── experiments ───────────────────────────────────────────

    def experiments(self) -> list[dict[str, Any]]:
        from strategy.research.storage import list_experiments

        try:
            return [exp.to_dict() for exp in list_experiments(self._data_dir)]
        except Exception:
            return []

    def create_experiment(
        self,
        strategy_id: str,
        version_id: str,
        execution_ids: list[str],
        hypothesis: str,
        configuration: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        from strategy.research.experiment import create_experiment
        from strategy.research.storage import save_experiment

        experiment = create_experiment(
            str(strategy_id),
            str(version_id),
            [str(e) for e in execution_ids],
            str(hypothesis),
            dict(configuration or {}),
        )
        save_experiment(experiment, self._data_dir)
        return experiment.to_dict()

    # ── analysis ──────────────────────────────────────────────

    def _quality_for(self, strategy_id: str) -> dict[str, Any]:
        """Honest data-quality view: only what the dataset actually carries."""
        dataset = self.get_dataset(str(strategy_id))
        if dataset is None:
            return {
                "bars": "N/A",
                "symbols": "N/A",
                "date_range": "N/A",
                "missing_bars": "NOT CHECKED",
                "duplicate_bars": "NOT CHECKED",
                "timezone": "N/A",
                "data_status": "NO DATA",
            }
        identity = dict(getattr(dataset, "data_identity", {}) or {})
        symbol = str(identity.get("symbol", "") or "")
        start = str(
            identity.get("start_date", identity.get("start", identity.get("from", ""))) or ""
        )
        end = str(identity.get("end_date", identity.get("end", identity.get("to", ""))) or "")
        date_range = f"{start} → {end}" if start or end else "N/A"
        return {
            "bars": "N/A",  # execution histories carry trades, not bar counts
            "symbols": symbol or "N/A",
            "date_range": date_range,
            "missing_bars": "NOT CHECKED",
            "duplicate_bars": "NOT CHECKED",
            "timezone": "N/A",
            "data_status": "READY" if getattr(dataset, "trades", ()) else "NO TRADES",
        }

    def _repro_for(self, strategy_id: str, experiment_id: str | None = None) -> dict[str, Any]:
        """Reproducibility facts: every result traces to strategy/dataset/params."""
        dataset = self.get_dataset(str(strategy_id))
        if dataset is None:
            return {
                "experiment_id": experiment_id or "N/A",
                "strategy_version": "N/A",
                "dataset": "N/A",
                "dataset_version": "N/A",
                "parameters": "N/A",
                "timeframe": "N/A",
                "date_range": "N/A",
            }
        identity = dict(getattr(dataset, "data_identity", {}) or {})
        timeframe = str(identity.get("timeframe", "") or "")
        start = str(
            identity.get("start_date", identity.get("start", identity.get("from", ""))) or ""
        )
        end = str(identity.get("end_date", identity.get("end", identity.get("to", ""))) or "")
        params = dict(getattr(dataset, "parameters", {}) or {})
        return {
            "experiment_id": experiment_id or "N/A",
            "strategy_version": str(getattr(dataset, "version_id", "") or "N/A"),
            "dataset": str(getattr(dataset, "strategy_id", str(strategy_id)) or "N/A"),
            "dataset_version": str(getattr(dataset, "version_id", "") or "N/A"),
            "parameters": params if params else "N/A",
            "timeframe": timeframe or "N/A",
            "date_range": f"{start} → {end}" if start or end else "N/A",
        }

    def data_quality(self, strategy_id: str) -> dict[str, Any]:
        """Public data-quality view (never raises, never fabricates)."""
        try:
            return self._quality_for(strategy_id)
        except Exception:
            return {
                "bars": "N/A",
                "symbols": "N/A",
                "date_range": "N/A",
                "missing_bars": "NOT CHECKED",
                "duplicate_bars": "NOT CHECKED",
                "timezone": "N/A",
                "data_status": "NO DATA",
            }

    def reproducibility(self, strategy_id: str, experiment_id: str | None = None) -> dict[str, Any]:
        """Public reproducibility view (never raises)."""
        try:
            return self._repro_for(strategy_id, experiment_id)
        except Exception:
            return {
                "experiment_id": experiment_id or "N/A",
                "strategy_version": "N/A",
                "dataset": "N/A",
                "dataset_version": "N/A",
                "parameters": "N/A",
                "timeframe": "N/A",
                "date_range": "N/A",
            }

    def list_trades(self, strategy_id: str) -> tuple[Any, ...]:
        """Real closed trades for a dataset (empty when none). Never raises."""
        try:
            dataset = self.get_dataset(str(strategy_id))
            if dataset is None:
                return ()
            return tuple(getattr(dataset, "trades", ()) or ())
        except Exception:
            return ()

    def list_signals(self, strategy_id: str) -> tuple[Any, ...]:
        """Real strategy signals for a dataset (empty when none). Never raises."""
        try:
            dataset = self.get_dataset(str(strategy_id))
            if dataset is None:
                return ()
            return tuple(getattr(dataset, "signals", ()) or ())
        except Exception:
            return ()

    def run_robustness(self, strategy_id: str) -> list[dict[str, Any]]:
        """Real robustness suite via `strategy.research`. Empty = NOT RUN/NO DATA."""
        from strategy.research.robustness import run_robustness

        dataset = self.get_dataset(str(strategy_id))
        if dataset is None:
            return []
        try:
            results = run_robustness(dataset, None)
        except Exception:
            return []
        out: list[dict[str, Any]] = []
        for item in results or []:
            try:
                out.append(
                    {
                        "test_type": str(getattr(item, "test_type", "unknown")),
                        "stability": str(getattr(item, "stability", "WARNING")),
                        "input": dict(getattr(item, "input", {}) or {}),
                        "result": dict(getattr(item, "result", {}) or {}),
                        "evidence": dict(getattr(item, "evidence", {}) or {}),
                    }
                )
            except Exception:
                continue
        return out

    def run_validation(self, strategy_id: str) -> dict[str, Any]:
        """Real validation gate via `strategy.research`. NOT RUN when no data."""
        from strategy.research.analysis import analyze_dataset
        from strategy.research.robustness import run_robustness
        from strategy.research.validation import validate_experiment

        dataset = self.get_dataset(str(strategy_id))
        if dataset is None:
            return {"status": "NOT RUN", "summary": "No dataset: nothing to validate."}
        try:
            analysis = analyze_dataset(dataset)
            robustness = run_robustness(dataset, None)
            verdict = validate_experiment(analysis, robustness)
            return {
                "status": str(verdict.status),
                "summary": str(verdict.summary),
                "details": dict(getattr(verdict, "details", {}) or {}),
            }
        except Exception as exc:
            return {"status": "FAILED", "summary": f"Validation failed: {exc}"}

    def comparison(self) -> list[dict[str, Any]]:
        """Experiment comparison rows — data-driven, NO RESULT when empty."""
        rows: list[dict[str, Any]] = []
        try:
            experiments = self.experiments()
        except Exception:
            experiments = []
        for exp in experiments:
            if not isinstance(exp, dict):
                continue
            result = exp.get("result")
            rows.append(
                {
                    "experiment_id": str(exp.get("experiment_id", "N/A")),
                    "strategy_id": str(exp.get("strategy_id", "N/A")),
                    "version_id": str(exp.get("version_id", "N/A")),
                    "status": "HAS RESULT" if result else "NO RESULT",
                    "result": result if isinstance(result, dict) else {},
                }
            )
        return rows

    def run_analysis(self, strategy_id: str) -> AnalysisView:
        """Analyze one dataset with the real engine. Unknown → honest notes."""
        from strategy.research.analysis import analyze_dataset

        dataset = self.get_dataset(str(strategy_id))
        if dataset is None:
            return AnalysisView(
                strategy_id=str(strategy_id),
                version_id="",
                notes=("No dataset: no execution histories for this strategy.",),
                status="NO DATA",
                data_quality=self.data_quality(str(strategy_id)),
                reproducibility=self.reproducibility(str(strategy_id)),
            )
        try:
            result = analyze_dataset(dataset)
        except Exception as exc:
            return AnalysisView(
                strategy_id=strategy_id,
                version_id=str(getattr(dataset, "version_id", "")),
                notes=(f"Analysis failed: {exc}",),
                status="FAILED",
                data_quality=self._quality_for(str(strategy_id)),
                reproducibility=self._repro_for(str(strategy_id)),
            )
        metrics = {
            # Legacy keys (workspace + tests depend on these names).
            "trades": result.trade_count,
            "signals": result.signal_count,
            "win_rate": _metric(result.win_rate),
            "avg_win": _metric(result.avg_win),
            "avg_loss": _metric(result.avg_loss),
            "expectancy": _metric(result.expectancy),
            "profit_factor": _metric(result.profit_factor),
            "net_profit": _metric(result.net_profit),
            "drawdown_pct": _metric(result.drawdown_pct),
            "sharpe": _metric(result.sharpe),
            # Institutional extensions — real engine values or "N/A".
            "total_trades": result.trade_count,
            "max_drawdown": _metric(getattr(result, "max_drawdown_abs", None)),
            "drawdown_abs": _metric(getattr(result, "max_drawdown_abs", None)),
            "sortino": _metric(getattr(result, "sortino", None)),
            "cagr": _metric(getattr(result, "cagr", None)),
            "volatility": _metric(getattr(result, "volatility", None)),
            "payoff_ratio": _metric(getattr(result, "payoff_ratio", None)),
            "exposure": _metric(getattr(result, "exposure", None)),
            "turnover": _metric(getattr(result, "turnover", None)),
        }
        notes: list[str] = []
        if result.trade_count == 0 and result.signal_count == 0:
            status = "NO DATA"
            notes.append("No trades or signals in dataset: nothing to analyze.")
        elif result.trade_count == 0:
            status = "NO TRADES"
            notes.append("No trades in dataset: metrics are structural, not results.")
        else:
            status = "COMPLETE"
        if result.drawdown_pct is None and result.trade_count:
            notes.append("Drawdown % unavailable: running peak never positive.")
        if result.sharpe is None and result.trade_count:
            notes.append("Sharpe unavailable: fewer than 2 usable trade returns.")
        if getattr(result, "sortino", None) is None and result.trade_count:
            notes.append("Sortino unavailable: no downside deviation in dataset.")
        if getattr(result, "cagr", None) is None:
            notes.append("CAGR unavailable: dataset carries no dated equity curve.")
        if getattr(result, "exposure", None) is None:
            notes.append("Exposure/turnover unavailable: no position-sizing timeline.")
        return AnalysisView(
            strategy_id=strategy_id,
            version_id=str(getattr(dataset, "version_id", "")),
            metrics=metrics,
            notes=tuple(notes),
            status=status,
            trades=tuple(getattr(dataset, "trades", ()) or ()),
            signals=tuple(getattr(dataset, "signals", ()) or ()),
            data_quality=self._quality_for(str(strategy_id)),
            reproducibility=self._repro_for(str(strategy_id)),
        )


__all__ = ["AnalysisView", "DatasetSummary", "ResearchService"]
