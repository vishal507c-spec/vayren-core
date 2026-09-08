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
    """Flattened analysis result for display (all values backend-derived)."""

    strategy_id: str
    version_id: str
    metrics: dict[str, Any] = field(default_factory=dict)
    notes: tuple[str, ...] = ()


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

    def run_analysis(self, strategy_id: str) -> AnalysisView:
        """Analyze one dataset with the real engine. Unknown → honest notes."""
        from strategy.research.analysis import analyze_dataset

        dataset = self.get_dataset(str(strategy_id))
        if dataset is None:
            return AnalysisView(
                strategy_id=str(strategy_id),
                version_id="",
                notes=("No dataset: no execution histories for this strategy.",),
            )
        try:
            result = analyze_dataset(dataset)
        except Exception as exc:
            return AnalysisView(
                strategy_id=strategy_id,
                version_id=str(getattr(dataset, "version_id", "")),
                notes=(f"Analysis failed: {exc}",),
            )
        metrics = {
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
        }
        notes: list[str] = []
        if result.drawdown_pct is None:
            notes.append("Drawdown unavailable: dataset carries no equity curve.")
        if result.sharpe is None:
            notes.append("Sharpe unavailable: fewer than 2 usable trade returns.")
        if not result.trade_count:
            notes.append("No trades in dataset: metrics are structural, not results.")
        return AnalysisView(
            strategy_id=strategy_id,
            version_id=str(getattr(dataset, "version_id", "")),
            metrics=metrics,
            notes=tuple(notes),
        )


__all__ = ["AnalysisView", "DatasetSummary", "ResearchService"]
