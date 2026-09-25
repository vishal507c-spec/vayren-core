"""ResearchService — composition service behind the Research workspace.

Wires the Python research engine (`strategy.research`) to datasets built
from persisted backtest execution histories. No computation lives in the
UI; this service owns dataset assembly, experiment persistence and
analysis runs. All external access goes through injected callables so the
workspace is testable without a strategy library on disk.

Research logic stays Python per AI_ENTRY.md §1.
"""

from __future__ import annotations

import contextlib
import hashlib
import inspect
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
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


RESEARCH_SIDES = ("LONG", "SHORT", "BOTH")

TIMEFRAME_LADDER = ("1m", "3m", "5m", "15m", "30m", "45m", "1h", "2h", "4h", "1D", "1W")


class SignalRecordingLogic:
    """Wraps canonical compiled strategy logic and records the executed signal stream.

    Passed straight into the canonical ``execute_bars`` core, so position
    management, fills and trade economics are untouched — this only observes
    the signals that actually drove execution (position-aware, same run as
    the trades). Visual/state helpers delegate to the wrapped logic.
    """

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.recorded: list[dict[str, Any]] = []

    def warmup(self) -> int:
        try:
            return max(0, int(self._inner.warmup()))
        except Exception:
            return 0

    def on_bar(self, view: Any) -> Any:
        signal = self._inner.on_bar(view)
        if signal is not None:
            try:
                kind = getattr(signal, "kind", None)
                kind_value = getattr(kind, "value", kind)
                self.recorded.append(
                    {
                        "index": int(getattr(signal, "index", getattr(view, "index", -1))),
                        "timestamp": str(getattr(signal, "timestamp", "")),
                        "kind": str(kind_value),
                        "price": float(getattr(signal, "price", 0.0) or 0.0),
                        "stop_loss": getattr(signal, "stop_loss", None),
                        "take_profit": getattr(signal, "take_profit", None),
                    }
                )
            except Exception:
                pass
        return signal

    def __getattr__(self, name: str) -> Any:
        return getattr(self.__dict__["_inner"], name)


def _window_bounds(start_date: str, end_date: str) -> tuple[str, str]:
    """Aggregation-window bounds — the backtest kernel owns the margin rule."""
    from backtest.native_runner import window_bounds

    return window_bounds(start_date[:10], end_date[:10])


def _source_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _spec_number(spec: Any, key: str, default: float) -> float:
    """Read one numeric spec field (specs are untyped mappings; be strict)."""
    try:
        raw = spec.get(key, default) if isinstance(spec, dict) else default
    except Exception:
        return default
    if raw is None:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def validate_research_config(
    config: dict[str, Any], known_param_keys: tuple[str, ...] | list[str] = ()
) -> list[str]:
    """Validate a research configuration; empty list means READY to execute."""
    errors: list[str] = []
    if not str(config.get("strategy_name", "") or "").strip():
        errors.append("No strategy selected — select a strategy first.")
    symbols = config.get("symbols", ())
    symbols = [str(s).strip() for s in (symbols or []) if str(s).strip()]
    if not symbols:
        errors.append("No symbols — set a universe with at least one symbol.")
    timeframe = str(config.get("timeframe", "") or "")
    if not timeframe:
        errors.append("No timeframe selected.")
    elif timeframe not in TIMEFRAME_LADDER:
        errors.append(f"Unknown timeframe '{timeframe}' — use the canonical ladder.")
    start = str(config.get("start_date", "") or "")
    end = str(config.get("end_date", "") or "")
    if not start or not end:
        errors.append("Date range incomplete — set both start and end dates.")
    elif start > end:
        errors.append("Start date is after end date.")
    try:
        capital = float(config.get("initial_capital", 0) or 0)
    except (TypeError, ValueError):
        capital = 0.0
    if capital <= 0:
        errors.append("Initial capital must be positive.")
    for key in ("slippage_pct", "commission_pct"):
        try:
            value = float(config.get(key, 0) or 0)
        except (TypeError, ValueError):
            errors.append(f"{key} must be a number.")
            continue
        if value < 0 or value > 5:
            errors.append(f"{key} must be between 0 and 5 percent.")
    if str(config.get("side", "BOTH") or "BOTH").upper() not in RESEARCH_SIDES:
        errors.append("Side must be LONG, SHORT or BOTH.")
    params = config.get("parameters", {})
    if not isinstance(params, dict):
        errors.append("Parameters must be a key/value mapping.")
    elif known_param_keys:
        known = set(known_param_keys)
        unknown = [k for k in params if k not in known]
        if unknown:
            errors.append(f"Unknown parameters: {sorted(unknown)} (valid: {sorted(known)}).")
    return errors


class ResearchService:
    """Dataset assembly + experiment store + analysis runs."""

    def __init__(
        self,
        data_dir: str | Path | None = None,
        strategy_dir: str | Path | None = None,
        list_strategies_fn: Callable[[Any], list[str]] | None = None,
        list_histories_fn: Callable[[Any], list[Any]] | None = None,
        repository: Any | None = None,
        compile_fn: Callable[[str], Any] | None = None,
        load_record_fn: Callable[..., Any] | None = None,
        execute_bars_fn: Callable[..., Any] | None = None,
    ) -> None:
        self._data_dir = data_dir
        self._strategy_dir = strategy_dir
        self._list_strategies = list_strategies_fn
        self._list_histories = list_histories_fn
        self._repository_override = repository
        self._compile_fn = compile_fn
        self._load_record_fn = load_record_fn
        self._execute_bars_fn = execute_bars_fn
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

    # ── real research workflow ──────────────────────────────────
    # Canonical strategy + data + backtest core. Research stays an analytical
    # layer: execution reuses BacktestRunner.execute_bars with a
    # signal-recording wrapper (same run drives signals and trades).

    def _resolve_repository(self) -> Any | None:
        if self._repository_override is not None:
            return self._repository_override
        try:
            from market.repository.symbol_repository import SymbolRepository  # pyright: ignore
        except Exception:
            return None
        try:
            if self._data_dir is None:
                return None
            return SymbolRepository(self._data_dir)
        except Exception:
            return None

    def _resolve_compile(self) -> Any | None:
        if self._compile_fn is not None:
            return self._compile_fn
        try:
            from strategy.language.compiler import compile_strategy
        except Exception:
            return None
        return compile_strategy

    def _resolve_load_record(self) -> Any | None:
        if self._load_record_fn is not None:
            return self._load_record_fn
        try:
            from strategy.language.storage import load_strategy_record
        except Exception:
            return None
        return load_strategy_record

    def _resolve_execute_bars(self) -> Any | None:
        if self._execute_bars_fn is not None:
            return self._execute_bars_fn
        try:
            from backtest.runner import execute_bars  # pyright: ignore[reportMissingImports]
        except Exception:
            return None
        return execute_bars

    def _engine_source_hash(self) -> str:
        try:
            from backtest.runner import execute_bars  # pyright: ignore[reportMissingImports]
        except Exception:
            return ""
        try:
            return _source_hash(inspect.getsource(execute_bars))
        except Exception:
            return ""

    def available_strategies(self) -> list[str]:
        """Canonical strategy names from the strategy library."""
        return self._strategy_names()

    def available_symbols(self) -> list[str]:
        """Canonical symbols from the market store (empty when unavailable)."""
        repository = self._resolve_repository()
        if repository is None:
            return []
        try:
            return [str(s) for s in repository.list_symbols()]
        except Exception:
            return []

    def describe_strategy(self, name: str) -> dict[str, Any] | None:
        """Load the REAL canonical strategy definition (never a copy).

        Returns identity, version, parameter specs/defaults and source hash,
        or None when the strategy is not in the library.
        """
        load_record = self._resolve_load_record()
        compile_fn = self._resolve_compile()
        if load_record is None or compile_fn is None:
            return None
        record = None
        try:
            record = load_record(str(name), self._strategy_dir)
        except Exception:
            record = None
        if record is None:
            try:
                from strategy.language.storage import get_strategy_by_id

                record = get_strategy_by_id(str(name), self._strategy_dir)
            except Exception:
                record = None
        if record is None:
            return None
        code = str(getattr(record, "code", "") or "")
        try:
            compiled = compile_fn(code)
        except Exception as exc:
            return {
                "id": str(getattr(record, "id", name)),
                "name": str(getattr(record, "name", name)),
                "compile_error": str(exc),
            }
        specs: dict[str, dict[str, Any]] = {}
        try:
            for spec in getattr(compiled, "param_specs", ()) or ():
                specs[str(spec.key)] = {
                    "key": str(spec.key),
                    "label": str(spec.label),
                    "default": float(spec.default),
                    "minimum": float(spec.minimum),
                    "maximum": float(spec.maximum),
                    "decimals": int(spec.decimals),
                }
        except Exception:
            specs = {}
        version = str(getattr(record, "version", "") or "")
        strategy_id = str(getattr(record, "id", name))
        try:
            from strategy.version import list_versions

            versions = list_versions(strategy_id, self._strategy_dir)
            if versions:
                version = str(getattr(versions[-1], "version_id", "") or version)
        except Exception:
            pass
        return {
            "id": strategy_id,
            "name": str(getattr(record, "name", name)),
            "version": version or "1.0",
            "parameters": specs,
            "defaults": dict(getattr(compiled, "param_defaults", {}) or {}),
            "source_hash": _source_hash(code),
        }

    def validate_config(self, config: dict[str, Any]) -> list[str]:
        """Validate a research configuration against strategy + ladder rules."""
        known: list[str] = []
        try:
            described = self.describe_strategy(str(config.get("strategy_name", "") or ""))
        except Exception:
            described = None
        if described is not None and "compile_error" not in described:
            params = described.get("parameters", {})
            if isinstance(params, dict):
                known = sorted(params)
                labels = sorted(
                    str(v.get("label", ""))
                    for v in params.values()
                    if isinstance(v, dict) and v.get("label")
                )
                known = sorted(set(known) | set(labels))
        return validate_research_config(dict(config or {}), known)

    def _canonical_config(self, config: dict[str, Any], source_hash: str = "") -> dict[str, Any]:
        symbols: list[str] = []
        for symbol in config.get("symbols", ()) or ():
            text = str(symbol).strip()
            if text and text not in symbols:
                symbols.append(text)
        parameters: dict[str, float] = {}
        raw_params = config.get("parameters", {}) or {}
        if isinstance(raw_params, dict):
            for key, value in raw_params.items():
                try:
                    parameters[str(key)] = float(value)  # type: ignore[arg-type]
                except (TypeError, ValueError):
                    continue
        return {
            "strategy_id": str(config.get("strategy_name", "") or ""),
            "strategy_version": str(config.get("strategy_version", "") or ""),
            "strategy_source_hash": str(source_hash),
            "universe": str(config.get("universe", "") or ""),
            "symbols": symbols,
            "timeframe": str(config.get("timeframe", "") or ""),
            "start_date": str(config.get("start_date", "") or "")[:10],
            "end_date": str(config.get("end_date", "") or "")[:10],
            "side": str(config.get("side", "BOTH") or "BOTH").upper(),
            "initial_capital": float(config.get("initial_capital", 0) or 0),
            "slippage_pct": float(config.get("slippage_pct", 0) or 0),
            "commission_pct": float(config.get("commission_pct", 0) or 0),
            "parameters": parameters,
        }

    @staticmethod
    def _effective_params(described: dict[str, Any] | None, wanted: Any) -> dict[str, float]:
        """User overrides merged over strategy defaults (same merge as execution).

        Creation and execution must fingerprint identical configurations, so
        both paths share this merge: defaults first, then label-aware overrides.
        """
        described = described or {}
        defaults = described.get("defaults", {}) or {}
        specs = described.get("parameters", {}) or {}
        if not isinstance(specs, dict):
            specs = {}
        label_to_key = {
            str(v.get("label", "")): key
            for key, v in specs.items()
            if isinstance(v, dict) and v.get("label")
        }
        merged: dict[str, float] = {}
        if isinstance(defaults, dict):
            for key, value in defaults.items():
                try:
                    merged[str(key)] = float(value)  # type: ignore[arg-type]
                except (TypeError, ValueError):
                    continue
        if isinstance(wanted, dict):
            for key, value in wanted.items():
                target = label_to_key.get(str(key), str(key))
                try:
                    merged[target] = float(value)  # type: ignore[arg-type]
                except (TypeError, ValueError):
                    continue
        if specs:
            # The compiler registers every default under both key and label;
            # the canonical identity keeps key spellings only (values equal).
            labels = {str(v.get("label", "")) for v in specs.values() if isinstance(v, dict)}
            for label in labels:
                if label in merged and label not in specs:
                    merged.pop(label, None)
        return merged

    def current_fingerprint(self, config: dict[str, Any]) -> str | None:
        """Fingerprint the current form configuration (STALE detection)."""
        from strategy.research.fingerprint import fingerprint_config

        try:
            described = self.describe_strategy(str(config.get("strategy_name", "") or ""))
        except Exception:
            described = None
        source_hash = ""
        if described is not None:
            source_hash = str(described.get("source_hash", "") or "")
            if not config.get("strategy_version") and described.get("version"):
                config = {**config, "strategy_version": described.get("version")}
            config = {
                **config,
                "parameters": self._effective_params(described, config.get("parameters", {})),
            }
        try:
            return fingerprint_config(self._canonical_config(config, source_hash))
        except Exception:
            return None

    def create_research_experiment(self, config: dict[str, Any]) -> dict[str, Any]:
        """Persist a DRAFT experiment (hypothesis + full configuration)."""
        from strategy.research.experiment import create_experiment
        from strategy.research.fingerprint import fingerprint_config
        from strategy.research.storage import save_experiment, save_experiment_update

        errors = self.validate_config(dict(config or {}))
        described = None
        try:
            described = self.describe_strategy(str(config.get("strategy_name", "") or ""))
        except Exception:
            described = None
        if described is None:
            errors = [
                f"Unknown strategy '{config.get('strategy_name', '')}' — not in library.",
                *errors,
            ]
        source_hash = str((described or {}).get("source_hash", "") or "")
        version = str(config.get("strategy_version", "") or (described or {}).get("version", ""))
        effective = self._effective_params(described, config.get("parameters", {}))
        canonical = self._canonical_config(
            {**config, "strategy_version": version, "parameters": effective}, source_hash
        )
        configuration = {
            **canonical,
            "research_question": str(config.get("research_question", "") or ""),
            "expected_direction": str(config.get("expected_direction", "") or ""),
            "expected_effect": str(config.get("expected_effect", "") or ""),
            "conditions": list(config.get("conditions", ()) or ()),
            "assumptions": list(config.get("assumptions", ()) or ()),
        }
        hypothesis = config.get("hypothesis", {})
        hypothesis_text = (
            hypothesis.get("text", "") if isinstance(hypothesis, dict) else str(hypothesis or "")
        )
        experiment = create_experiment(
            canonical["strategy_id"],
            version,
            [],
            hypothesis_text,
            configuration,
            research_question=configuration["research_question"],
            expected_direction=configuration["expected_direction"],
            expected_effect=configuration["expected_effect"],
            conditions=configuration["conditions"],
            assumptions=configuration["assumptions"],
            strategy_version=version,
            universe=canonical["universe"],
            symbols=canonical["symbols"],
            timeframe=canonical["timeframe"],
            start_date=canonical["start_date"],
            end_date=canonical["end_date"],
            side=canonical["side"],
            initial_capital=canonical["initial_capital"],
            slippage_pct=canonical["slippage_pct"],
            commission_pct=canonical["commission_pct"],
            parameters=canonical["parameters"],
        )
        try:
            experiment.config_fingerprint = fingerprint_config(canonical)
        except Exception:
            experiment.config_fingerprint = ""
        if errors:
            experiment.status = "INVALID"
            experiment.reproducibility = {"validation_errors": errors}
        try:
            save_experiment(experiment, self._data_dir)
        except Exception:
            save_experiment_update(experiment, self._data_dir)
        return experiment.to_dict()

    def get_experiment(self, experiment_id: str) -> dict[str, Any] | None:
        """Load one persisted experiment (None when missing)."""
        try:
            from strategy.research.storage import load_experiment
        except Exception:
            return None
        try:
            experiment = load_experiment(str(experiment_id), self._data_dir)
        except Exception:
            return None
        return experiment.to_dict() if experiment is not None else None

    # ── execution ─────────────────────────────────────────────

    def _execute_symbols(
        self,
        compiled: Any,
        params: Any,
        repository: Any,
        symbols: list[str],
        timeframe: str,
        start_date: str,
        end_date: str,
        initial_capital: float,
        slippage_pct: float,
        commission_pct: float,
        strategy_name: str,
        experiment_id: str,
        execute_bars: Any,
        collect_signals: bool,
        progress: Any | None,
        is_cancelled: Any | None,
        stage: str,
        say: Any | None,
    ) -> tuple[list[Any], list[dict[str, Any]], dict[str, tuple[Any, ...]], dict[str, Any]]:
        """Run the canonical core per symbol; returns (trades, signals, windows, ref)."""
        from backtest.engine.replay import slice_bars  # pyright: ignore[reportMissingImports]
        from backtest.models.config import BacktestConfig  # pyright: ignore[reportMissingImports]

        lower, upper = _window_bounds(start_date, end_date)
        all_trades: list[Any] = []
        all_signals: list[dict[str, Any]] = []
        windows: dict[str, tuple[Any, ...]] = {}
        reference: dict[str, Any] = {}
        failed: dict[str, str] = {}
        total = max(1, len(symbols))
        for done, symbol in enumerate(symbols, start=1):
            if callable(is_cancelled) and is_cancelled():
                if callable(say):
                    say("Cancellation requested — stopping at a safe checkpoint.")
                break
            try:
                bars = repository.get_candles_timeframe(symbol, timeframe, None, lower, upper)
            except Exception as exc:
                failed[symbol] = f"data unavailable: {exc}"
                continue
            window = slice_bars(tuple(bars or ()), start_date, end_date)
            if not window:
                failed[symbol] = "no bars in requested range"
                continue
            windows[symbol] = window
            first = window[0]
            last = window[-1]
            reference[symbol] = {
                "bars": len(window),
                "first": first.timestamp if not isinstance(first, dict) else first.get("timestamp"),
                "last": last.timestamp if not isinstance(last, dict) else last.get("timestamp"),
            }
            try:
                logic = compiled.create_logic(params, owner_id=strategy_name)
            except Exception as exc:
                failed[symbol] = f"strategy failed: {exc}"
                windows.pop(symbol, None)
                reference.pop(symbol, None)
                continue
            recorder = SignalRecordingLogic(logic) if collect_signals else None
            active = recorder if recorder is not None else logic
            config = BacktestConfig(
                symbol=symbol,
                timeframe=timeframe,
                start_date=start_date,
                end_date=end_date,
                initial_capital=initial_capital,
                slippage_pct=slippage_pct,
                commission_pct=commission_pct,
            )
            try:
                trades = execute_bars(active, window, params, config)
            except Exception as exc:
                failed[symbol] = f"execution failed: {exc}"
                windows.pop(symbol, None)
                reference.pop(symbol, None)
                continue
            for trade in trades or ():
                all_trades.append(trade)
            if recorder is not None:
                for record in recorder.recorded:
                    all_signals.append(
                        {
                            "time": str(record.get("timestamp", "")),
                            "symbol": symbol,
                            "timeframe": timeframe,
                            "side": str(record.get("kind", "")),
                            "price": record.get("price", ""),
                            "event": str(record.get("kind", "SIGNAL")),
                            "strategy": strategy_name,
                            "experiment": experiment_id,
                        }
                    )
            if callable(progress):
                with contextlib.suppress(Exception):
                    progress(stage, done, total, f"{stage}: {symbol} ({done}/{total})")
        return all_trades, all_signals, windows, {"per_symbol": reference, "failed": failed}

    def run_experiment(
        self,
        experiment_id: str,
        progress: Callable[..., None] | None = None,
        is_cancelled: Callable[[], bool] | None = None,
        on_log: Callable[[str], None] | None = None,
    ) -> tuple[dict[str, Any] | None, list[str]]:
        """Execute one DRAFT/INVALID experiment end-to-end (real computation).

        Never fabricates: every number comes from canonical strategy logic +
        canonical market data + the shared backtest core. Returns the persisted
        experiment dict and the engine log lines.
        """
        from strategy.research.advanced_validation import (
            check_data_leakage,
            check_temporal_stability,
            compute_dsr,
            compute_pbo,
            correct_multiple_testing,
            grade_evidence,
            run_cpcv,
            validate_costs,
            validate_oos,
        )
        from strategy.research.analysis import analyze_dataset
        from strategy.research.conditions import analyze_conditions
        from strategy.research.data_validation import validate_bars
        from strategy.research.dataset import ResearchDataset
        from strategy.research.experiment import TERMINAL_STATUSES
        from strategy.research.fingerprint import fingerprint_config, fingerprint_result
        from strategy.research.report import build_report
        from strategy.research.statistics import (
            compare_buy_hold,
            describe_evidence,
            overfitting_warnings,
            run_monte_carlo,
        )
        from strategy.research.storage import (
            load_experiment,
            save_experiment_update,
            save_run_artifacts,
        )
        from strategy.research.validation import validate_experiment
        from strategy.research.walkforward import (
            split_trades_chronological,
            summarize_walkforward,
            walk_forward_windows,
        )

        log: list[str] = []

        def _say(message: str) -> None:
            log.append(message)
            if callable(on_log):
                with contextlib.suppress(Exception):
                    on_log(message)

        def _emit(stage: str, done: int, total: int, message: str) -> None:
            log.append(message)
            if callable(on_log):
                with contextlib.suppress(Exception):
                    on_log(message)
            if callable(progress):
                with contextlib.suppress(Exception):
                    progress(stage, done, total, message)

        try:
            experiment = load_experiment(str(experiment_id), self._data_dir)
        except Exception as exc:
            return None, [f"EXPERIMENT FAILED — load error: {exc}"]
        if experiment is None:
            return None, [f"EXPERIMENT NOT FOUND — {experiment_id}"]
        if experiment.status in TERMINAL_STATUSES and experiment.result_fingerprint:
            return experiment.to_dict(), [
                f"Experiment {experiment_id} is {experiment.status} — "
                "results are immutable; create a new experiment to re-run."
            ]

        def _persist(status: str) -> None:
            experiment.status = status
            try:
                save_experiment_update(experiment, self._data_dir)
            except Exception as exc:
                _say(f"persist warning: {exc}")

        strategy_name = str(experiment.strategy_id or "")
        version = str(experiment.strategy_version or experiment.version_id or "")
        symbols = [str(s) for s in (experiment.symbols or ()) if str(s)]
        timeframe = str(experiment.timeframe or "")
        start_date = str(experiment.start_date or "")
        end_date = str(experiment.end_date or "")
        side = str(experiment.side or "BOTH").upper()
        capital = float(experiment.initial_capital or 0)
        slippage = float(experiment.slippage_pct or 0)
        commission = float(experiment.commission_pct or 0)
        wanted_params = dict(experiment.parameters or {})

        _persist("RUNNING")
        _emit("LOADING STRATEGY", 0, 1, f"Strategy load: {strategy_name}")
        described = self.describe_strategy(strategy_name)
        if described is None:
            _persist("INVALID")
            message = f"INVALID STRATEGY CONFIGURATION — '{strategy_name}' not in library."
            _say(message)
            experiment.reproducibility = {"validation_errors": [message]}
            with contextlib.suppress(Exception):
                save_experiment_update(experiment, self._data_dir)
            return experiment.to_dict(), log
        if "compile_error" in described:
            _persist("INVALID")
            message = (
                f"INVALID STRATEGY CONFIGURATION — compile failed: {described['compile_error']}"
            )
            _say(message)
            return experiment.to_dict(), log
        source_hash = str(described.get("source_hash", "") or "")
        if not version:
            version = str(described.get("version", "") or "1.0")
        specs = described.get("parameters", {})
        if not isinstance(specs, dict):
            specs = {}
        try:
            compile_fn = self._resolve_compile()
            from strategy.language.compiler import compile_strategy as _canonical_compile

            compile_fn = compile_fn or _canonical_compile
            from strategy.language.storage import load_strategy_record as _load

            record = _load(strategy_name, self._strategy_dir)
            if record is None:
                from strategy.language.storage import get_strategy_by_id as _by_id

                record = _by_id(experiment.strategy_id, self._strategy_dir)
            compiled = compile_fn(str(record.code if record is not None else ""))
        except Exception as exc:
            _persist("INVALID")
            message = f"INVALID STRATEGY CONFIGURATION — compile failed: {exc}"
            _say(message)
            return experiment.to_dict(), log
        _say(
            f"Strategy loaded: {strategy_name} v{version} "
            f"({len(specs)} parameters, source {source_hash[:12]})"
        )

        defaults = dict(getattr(compiled, "param_defaults", {}) or {})
        merged: dict[str, float] = {}
        try:
            for key, value in defaults.items():
                merged[str(key)] = float(value)
        except (TypeError, ValueError):
            pass
        label_to_key = {str(v.get("label", "")): k for k, v in specs.items() if isinstance(v, dict)}
        param_errors: list[str] = []
        for key, value in wanted_params.items():
            target = label_to_key.get(str(key), str(key))
            if specs and target not in specs:
                param_errors.append(f"unknown parameter '{key}'")
                continue
            try:
                number = float(value)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                param_errors.append(f"parameter '{key}' must be numeric")
                continue
            spec = specs.get(target, {}) if isinstance(specs.get(target), dict) else {}
            try:
                if spec:
                    lo, hi = float(spec.get("minimum", number)), float(spec.get("maximum", number))
                    if number < lo or number > hi:
                        param_errors.append(f"parameter '{key}'={number} outside [{lo}, {hi}]")
                        continue
            except (TypeError, ValueError):
                pass
            merged[target] = number
        errors = validate_research_config(
            {
                "strategy_name": strategy_name,
                "symbols": symbols,
                "timeframe": timeframe,
                "start_date": start_date,
                "end_date": end_date,
                "initial_capital": capital,
                "slippage_pct": slippage,
                "commission_pct": commission,
                "side": side,
                "parameters": dict(wanted_params),
            },
            sorted(specs) if specs else (),
        )
        errors = [*errors, *param_errors]
        if errors:
            _persist("INVALID")
            for error in errors:
                _say(f"INVALID: {error}")
            experiment.reproducibility = {"validation_errors": errors}
            with contextlib.suppress(Exception):
                save_experiment_update(experiment, self._data_dir)
            return experiment.to_dict(), log

        try:
            from strategy.models.parameters import StrategyParameters

            params = StrategyParameters(merged)
        except Exception as exc:
            _persist("INVALID")
            message = f"INVALID STRATEGY CONFIGURATION — parameters rejected: {exc}"
            _say(message)
            return experiment.to_dict(), log

        repository = self._resolve_repository()
        if repository is None:
            _persist("FAILED")
            message = "EXPERIMENT FAILED — no market repository attached."
            _say(message)
            return experiment.to_dict(), log
        execute_bars = self._resolve_execute_bars()
        if execute_bars is None:
            _persist("FAILED")
            message = "EXPERIMENT FAILED — backtest core unavailable."
            _say(message)
            return experiment.to_dict(), log

        _emit("VALIDATING DATA", 0, max(1, len(symbols)), "Data validation started")
        lower, upper = _window_bounds(start_date, end_date)
        bars_by_symbol: dict[str, tuple[Any, ...]] = {}
        fetch_failed: dict[str, str] = {}
        for done, symbol in enumerate(symbols, start=1):
            if callable(is_cancelled) and is_cancelled():
                _persist("CANCELLED")
                _say("Experiment cancelled during data load.")
                return experiment.to_dict(), log
            try:
                from backtest.engine.replay import slice_bars as _slice  # pyright: ignore

                raw = repository.get_candles_timeframe(symbol, timeframe, None, lower, upper)
                bars_by_symbol[symbol] = _slice(tuple(raw or ()), start_date, end_date)
            except Exception as exc:
                fetch_failed[symbol] = str(exc)
            _emit(
                "VALIDATING DATA",
                done,
                max(1, len(symbols)),
                f"Loaded {symbol} ({done}/{len(symbols)})",
            )
        report_validation = validate_bars(bars_by_symbol, symbols, start_date, end_date)
        for symbol, error in fetch_failed.items():
            _say(f"DATA NOT AVAILABLE — {symbol}: {error}")
        if not report_validation.ok:
            _persist("INVALID")
            _say(f"DATA VALIDATION FAILED — {report_validation.summary}")
            for item in report_validation.symbols:
                if not item.ok:
                    kinds = sorted({issue.kind for issue in item.issues})
                    _say(f"{item.symbol}: {item.bar_count} bars [{', '.join(kinds)}]")
            if report_validation.missing_symbols:
                _say(f"Missing data: {', '.join(report_validation.missing_symbols)}")
            experiment.data_reference = {
                "per_symbol": {},
                "failed": {
                    **fetch_failed,
                    **dict.fromkeys(report_validation.missing_symbols, "missing"),
                },
            }
            with contextlib.suppress(Exception):
                save_experiment_update(experiment, self._data_dir)
            return experiment.to_dict(), log
        total_bars = sum(len(b) for b in bars_by_symbol.values())
        _say(f"Data validation passed — {len(bars_by_symbol)} symbols, {total_bars} bars")
        if total_bars == 0:
            _persist("NO_DATA")
            _say("NO DATA — no bars in the requested range.")
            return experiment.to_dict(), log

        _emit("GENERATING SIGNALS", 0, max(1, len(symbols)), "Signal generation started")
        executed_at = datetime.now(UTC).isoformat()
        trades, signals, windows, ref = self._execute_symbols(
            compiled,
            params,
            repository,
            symbols,
            timeframe,
            start_date,
            end_date,
            capital,
            slippage,
            commission,
            strategy_name,
            experiment.experiment_id,
            execute_bars,
            True,
            progress,
            is_cancelled,
            "GENERATING SIGNALS",
            _say,
        )
        if callable(is_cancelled) and is_cancelled():
            _persist("CANCELLED")
            _say("Experiment cancelled during execution.")
            return experiment.to_dict(), log
        _emit(
            "GENERATING TRADES",
            len(windows),
            max(1, len(symbols)),
            f"Trade generation completed — {len(trades)} trades",
        )
        _say(f"Signal generation completed — {len(signals)} signals generated")

        if side != "BOTH":
            want_trade = side
            want_signal = "BUY" if side == "LONG" else "SELL"
            trades = [
                t for t in trades if str(self._trade_field(t, "side", "")).upper() == want_trade
            ]
            signals = [s for s in signals if str(s.get("side", "")).upper() == want_signal]
            _say(f"Side filter applied: {side} — {len(trades)} trades, {len(signals)} signals")

        if not trades and not signals:
            _persist("NO_DATA")
            _say("NO DATA — strategy produced no signals or trades in range.")
            experiment.data_reference = ref
            with contextlib.suppress(Exception):
                save_experiment_update(experiment, self._data_dir)
            return experiment.to_dict(), log

        _persist("ANALYZING")
        _emit("ANALYZING", 0, 1, "Performance analysis started")
        ordered = sorted(
            trades,
            key=lambda t: (
                str(self._trade_field(t, "exit_time", "")),
                str(self._trade_field(t, "entry_time", "")),
            ),
        )
        try:
            from backtest.engine.metrics import (  # pyright: ignore
                compute_equity_curve,
                compute_metrics,
            )

            first_stamp = None
            for window in windows.values():
                if window:
                    stamp = (
                        window[0].timestamp
                        if not isinstance(window[0], dict)
                        else window[0].get("timestamp")
                    )
                    if first_stamp is None or str(stamp) < str(first_stamp):
                        first_stamp = str(stamp)
            curve = compute_equity_curve(tuple(ordered), capital, first_stamp)
            metrics = compute_metrics(tuple(ordered), curve, capital)
        except Exception as exc:
            _persist("FAILED")
            message = f"EXPERIMENT FAILED — metrics failed: {exc}"
            _say(message)
            return experiment.to_dict(), log

        dataset = ResearchDataset(
            strategy_id=strategy_name,
            version_id=version,
            execution_ids=(f"EXP-{experiment.experiment_id}",),
            trades=tuple(ordered),
            signals=tuple(signals),
            parameters={k: float(v) for k, v in merged.items()},
            data_identity={
                "universe": experiment.universe,
                "timeframe": timeframe,
                "start_date": start_date,
                "end_date": end_date,
                "symbol": ",".join(symbols),
                "symbols": list(symbols),
            },
            metadata={"experiment_id": experiment.experiment_id},
        )
        try:
            analysis = analyze_dataset(dataset)
        except Exception:
            analysis = None
        conditions = analyze_conditions(ordered, windows)
        stats = describe_evidence(ordered)
        montecarlo = run_monte_carlo(ordered)
        benchmark = compare_buy_hold(
            float(getattr(metrics, "net_profit", 0.0) or 0.0), capital, windows, symbols
        )
        _say("Performance analysis completed")

        _persist("VALIDATING_RESULT")
        _emit("RUNNING ROBUSTNESS", 0, 1, "Robustness analysis started")
        is_trades, oos_trades = split_trades_chronological(ordered, 0.7)
        oos_result = validate_oos(is_trades, oos_trades)
        cpcv_result = run_cpcv(dataset)
        pbo_result = compute_pbo(
            [cpcv_result] if cpcv_result.n_paths else None,
            n_trials=1,
            path_metrics=list(cpcv_result.path_metrics) if cpcv_result.n_paths else None,
        )
        pnls_for_dsr = [float(self._trade_field(t, "pnl", 0) or 0) for t in ordered]
        dsr_result = compute_dsr(
            float(getattr(metrics, "sharpe_ratio", 0.0) or 0.0)
            if getattr(metrics, "sharpe_ratio", None) is not None
            else None,
            1,
            pnls_for_dsr,
        )
        cost_result = validate_costs(list(ordered))
        temporal_result = check_temporal_stability(list(ordered))
        leakage_result = check_data_leakage(
            train_trades=list(is_trades), test_trades=list(oos_trades)
        )

        # Real parameter sensitivity: re-execute variants through the same core.
        # Perturb the first parameter with a nonzero base (a zero base cannot
        # be scaled); when every default is zero there is honestly nothing to
        # perturb and sensitivity reports that instead of fake variants.
        base_params = {k: float(v) for k, v in merged.items()}
        variant_axis: tuple[str, tuple[float, ...]] = ("", ())
        if specs:
            ordered_keys = sorted(specs)
            nonzero = [
                k
                for k in ordered_keys
                if float(base_params.get(k, _spec_number(specs[k], "default", 0.0))) != 0.0
            ]
            visual_hints = (
                "extend",
                "plot",
                "visual",
                "color",
                "colour",
                "display",
                "width",
                "style",
                "label",
            )
            trading = [
                k
                for k in nonzero
                if not any(h in f"{k} {specs[k].get('label', '')}".lower() for h in visual_hints)
            ]
            picked = next(iter(trading or nonzero), "")
            if picked:
                spec = specs[picked]
                base_value = float(base_params.get(picked, _spec_number(spec, "default", 0.0)))
                decimals = int(_spec_number(spec, "decimals", 6))
                variants: list[float] = []
                for factor in (0.8, 0.9, 1.0, 1.1, 1.2):
                    candidate = round(base_value * factor, decimals)
                    if candidate < _spec_number(
                        spec, "minimum", candidate
                    ) or candidate > _spec_number(spec, "maximum", candidate):
                        continue
                    if candidate not in variants:
                        variants.append(candidate)
                variant_axis = (picked, tuple(variants))
        real_sensitivity: list[dict[str, Any]] = []
        if variant_axis[0] and variant_axis[1]:
            from strategy.models.parameters import StrategyParameters as _Params

            base_exp = float(getattr(analysis, "expectancy", 0.0) or 0.0) if analysis else 0.0
            for done, value in enumerate(variant_axis[1], start=1):
                if callable(is_cancelled) and is_cancelled():
                    _persist("CANCELLED")
                    _say("Experiment cancelled during robustness.")
                    return experiment.to_dict(), log
                variant_params = dict(base_params)
                variant_params[variant_axis[0]] = float(value)
                try:
                    variant_trades, _, _, _ = self._execute_symbols(
                        compiled,
                        _Params(variant_params),
                        repository,
                        symbols,
                        timeframe,
                        start_date,
                        end_date,
                        capital,
                        slippage,
                        commission,
                        strategy_name,
                        experiment.experiment_id,
                        execute_bars,
                        False,
                        None,
                        is_cancelled,
                        "RUNNING ROBUSTNESS",
                        _say,
                    )
                except Exception as exc:
                    real_sensitivity.append(
                        {
                            "test_type": "parameter_sensitivity",
                            "input": {"param": variant_axis[0], "value": value},
                            "stability": "WARNING",
                            "result": {},
                            "evidence": {"error": str(exc)},
                        }
                    )
                    continue
                var_pnls = [float(self._trade_field(t, "pnl", 0) or 0) for t in variant_trades]
                var_exp = (sum(var_pnls) / len(var_pnls)) if var_pnls else None
                if base_exp is None or var_exp is None:
                    stability = "WARNING"
                elif abs(var_exp - base_exp) < 0.1 * abs(base_exp or 1):
                    stability = "PASS"
                elif var_exp * (base_exp or 0) < 0:
                    stability = "FAIL"
                else:
                    stability = "WARNING"
                wins = sum(1 for p in var_pnls if p > 0)
                real_sensitivity.append(
                    {
                        "test_type": "parameter_sensitivity",
                        "input": {"param": variant_axis[0], "value": value},
                        "stability": stability,
                        "result": {
                            "trade_count": len(var_pnls),
                            "win_rate": (wins / len(var_pnls)) if var_pnls else None,
                            "expectancy": var_exp,
                            "net_pnl": sum(var_pnls),
                        },
                        "evidence": {
                            "baseline_expectancy": base_exp,
                            "variant_expectancy": var_exp,
                        },
                    }
                )
                _emit(
                    "RUNNING ROBUSTNESS",
                    done,
                    max(1, len(variant_axis[1])),
                    f"Robustness variant {done}/{len(variant_axis[1])}: {variant_axis[0]}={value}",
                )
        else:
            real_sensitivity.append(
                {
                    "test_type": "parameter_sensitivity",
                    "input": {},
                    "stability": "WARNING",
                    "result": {},
                    "evidence": {
                        "note": "no nonzero parameter default to perturb — not applicable"
                    },
                }
            )
        _say("Robustness analysis completed")

        # Real walk-forward: execute each test window through the same core.
        folds = walk_forward_windows(start_date, end_date, 3)
        fold_expectancies: list[float | None] = []
        fold_rows: list[dict[str, Any]] = []
        for fold in folds:
            if callable(is_cancelled) and is_cancelled():
                _persist("CANCELLED")
                _say("Experiment cancelled during walk-forward.")
                return experiment.to_dict(), log
            try:
                from strategy.models.parameters import StrategyParameters as _Params2

                fold_trades, _, _, _ = self._execute_symbols(
                    compiled,
                    _Params2(dict(base_params)),
                    repository,
                    symbols,
                    timeframe,
                    fold.test_start,
                    fold.test_end,
                    capital,
                    slippage,
                    commission,
                    strategy_name,
                    experiment.experiment_id,
                    execute_bars,
                    False,
                    None,
                    is_cancelled,
                    "VALIDATING",
                    _say,
                )
            except Exception as exc:
                fold_rows.append({"fold": fold.fold, "error": str(exc)})
                fold_expectancies.append(None)
                continue
            fold_pnls = [float(self._trade_field(t, "pnl", 0) or 0) for t in fold_trades]
            fold_exp = (sum(fold_pnls) / len(fold_pnls)) if fold_pnls else None
            fold_expectancies.append(fold_exp)
            fold_rows.append(
                {
                    "fold": fold.fold,
                    "train": f"{fold.train_start} → {fold.train_end}",
                    "test": f"{fold.test_start} → {fold.test_end}",
                    "trades": len(fold_pnls),
                    "expectancy": fold_exp,
                }
            )
        walkforward = summarize_walkforward(fold_expectancies)

        _emit("VALIDATING", 0, 1, "Validation started")
        verdict = None
        if analysis is not None:
            try:
                verdict = validate_experiment(
                    analysis,
                    [],  # legacy gate unused; real validation is assembled below
                )
            except Exception:
                verdict = None
        variants_tested = 1 + len(real_sensitivity) + len(folds)
        unstable = sorted(
            {
                str(item["input"].get("param", ""))
                for item in real_sensitivity
                if item.get("stability") == "FAIL" and isinstance(item.get("input"), dict)
            }
        )
        warnings = overfitting_warnings(
            len(ordered), variants_tested, bool(oos_trades), tuple(unstable)
        )
        multiple = correct_multiple_testing(total_tested=variants_tested, selected=1)
        try:
            from strategy.research.advanced_validation import ValidationPolicy

            grade = grade_evidence(
                dataset,
                cpcv_result,
                pbo_result,
                dsr_result,
                multiple,
                None,
                ValidationPolicy(),
                oos_result,
                temporal_result,
                cost_result,
                leakage_result,
            )
        except Exception:
            grade = None
        has_oos = bool(oos_trades) and oos_result.status != "INSUFFICIENT_DATA"
        oos_positive = (oos_result.oos_expectancy or 0) > 0 if has_oos else None
        validation_status = getattr(grade, "status", None) or getattr(verdict, "status", "WARNING")
        validation_summary = "; ".join(
            [
                getattr(grade, "grade", "") or "",
                f"OOS {oos_result.status}",
                f"CPCV paths {cpcv_result.n_paths}",
                f"walk-forward {walkforward.status}",
            ]
        ).strip("; ")
        validation = {
            "status": str(validation_status),
            "summary": str(validation_summary or (getattr(verdict, "summary", "") or "")),
            "has_oos": has_oos,
            "oos_positive": oos_positive,
            "oos": oos_result.to_dict(),
            "cpcv": cpcv_result.to_dict(),
            "pbo": pbo_result.to_dict(),
            "dsr": dsr_result.to_dict(),
            "multiple_testing": multiple.to_dict(),
            "cost_stress": cost_result.to_dict(),
            "temporal": temporal_result.to_dict(),
            "leakage": leakage_result.to_dict(),
            "evidence_grade": grade.to_dict() if grade is not None else {},
            "walk_forward": {
                "status": walkforward.status,
                "folds": fold_rows,
                "mean_test_expectancy": walkforward.mean_test_expectancy,
                "note": walkforward.note,
            },
            "warnings": list(warnings),
        }
        _say(f"Validation completed — {validation_status}: {validation_summary}")

        _emit("FINALIZING", 0, 1, "Finalizing experiment")
        legs = [
            [
                str(self._trade_field(t, "entry_time", "")),
                str(self._trade_field(t, "exit_time", "")),
                str(self._trade_field(t, "side", "")),
                float(self._trade_field(t, "quantity", 0) or 0),
                float(self._trade_field(t, "pnl", 0) or 0),
            ]
            for t in ordered
        ]
        summary = {
            "trade_count": len(ordered),
            "signal_count": len(signals),
            "symbols_requested": list(symbols),
            "symbols_traded": sorted({str(self._trade_field(t, "symbol", "")) for t in ordered}),
            "symbols_failed": dict(ref.get("failed", {})),
            "net_pnl": float(getattr(metrics, "net_profit", 0.0) or 0.0),
            "return_pct": float(getattr(metrics, "net_profit_pct", 0.0) or 0.0),
            "win_rate": getattr(metrics, "win_rate", None),
            "profit_factor": getattr(metrics, "profit_factor", None),
            "expectancy": getattr(metrics, "expectancy", None),
            "avg_trade": getattr(metrics, "avg_trade", None),
            "avg_win": getattr(analysis, "avg_win", None) if analysis else None,
            "avg_loss": getattr(analysis, "avg_loss", None) if analysis else None,
            "max_drawdown_pct": float(getattr(metrics, "max_drawdown_pct", 0.0) or 0.0),
            "max_drawdown_abs": float(getattr(metrics, "max_drawdown_abs", 0.0) or 0.0),
            "sharpe": getattr(metrics, "sharpe_ratio", None),
            "sortino": getattr(analysis, "sortino", None) if analysis else None,
            "payoff_ratio": getattr(analysis, "payoff_ratio", None) if analysis else None,
            "gross_profit": float(getattr(metrics, "gross_profit", 0.0) or 0.0),
            "gross_loss": float(getattr(metrics, "gross_loss", 0.0) or 0.0),
            "starting_capital": capital,
            "ending_capital": float(getattr(metrics, "ending_capital", capital) or capital),
            "side_filter": side,
            "bars_used": total_bars,
            "period_start": start_date,
            "period_end": end_date,
            "trade_legs": legs,
        }
        canonical = self._canonical_config(
            {
                "strategy_name": strategy_name,
                "strategy_version": version,
                "universe": experiment.universe,
                "symbols": symbols,
                "timeframe": timeframe,
                "start_date": start_date,
                "end_date": end_date,
                "side": side,
                "initial_capital": capital,
                "slippage_pct": slippage,
                "commission_pct": commission,
                "parameters": self._effective_params(
                    {"defaults": {}, "parameters": specs}, base_params
                ),
            },
            source_hash,
        )
        config_fp = fingerprint_config(canonical)
        result_fp = fingerprint_result(summary)
        dataset_version = self._dataset_version(timeframe, start_date, end_date, ref, total_bars)
        engine_hash = self._engine_source_hash()
        reproducibility = {
            "experiment_id": experiment.experiment_id,
            "strategy_id": strategy_name,
            "strategy_version": version,
            "strategy_source_hash": source_hash,
            "parameters": dict(base_params),
            "universe": experiment.universe,
            "symbols": list(symbols),
            "timeframe": timeframe,
            "date_range": f"{start_date} → {end_date}",
            "initial_capital": capital,
            "slippage_pct": slippage,
            "commission_pct": commission,
            "side": side,
            "engine": "execute_bars",
            "engine_source_hash": engine_hash,
            "dataset_version": dataset_version,
            "data_reference": ref,
            "config_fingerprint": config_fp,
            "result_fingerprint": result_fp,
            "executed_at": executed_at,
        }
        condition_summary = {
            dimension: [self._bucket_dict(bucket) for bucket in buckets]
            for dimension, buckets in conditions.buckets.items()
        }
        symbol_dicts = [self._symbol_dict(row) for row in conditions.symbols]
        unsupported = list(conditions.unsupported)
        analysis_bundle: dict[str, Any] = {
            "conditions": {
                "buckets": condition_summary,
                "symbols": symbol_dicts,
                "unsupported": unsupported,
            },
        }
        stats_dict = {
            "n": stats.n,
            "mean": stats.mean,
            "ci_low": stats.ci_low,
            "ci_high": stats.ci_high,
            "ci_method": stats.ci_method,
            "t_stat": stats.t_stat,
            "p_value": stats.p_value,
            "p_method": stats.p_method,
            "cohens_d": stats.cohens_d,
            "win_rate": stats.win_rate,
            "win_rate_ci": list(stats.win_rate_ci),
            "status": stats.status,
            "notes": list(stats.notes),
        }
        montecarlo_dict = {
            "n_paths": montecarlo.n_paths,
            "seed": montecarlo.seed,
            "total_pnl_p5": montecarlo.total_pnl_p5,
            "total_pnl_p50": montecarlo.total_pnl_p50,
            "total_pnl_p95": montecarlo.total_pnl_p95,
            "max_dd_p50": montecarlo.max_dd_p50,
            "max_dd_p95": montecarlo.max_dd_p95,
            "prob_profit": montecarlo.prob_profit,
            "status": montecarlo.status,
            "note": montecarlo.note,
        }
        benchmark_dict = {
            "strategy_return_pct": benchmark.strategy_return_pct,
            "benchmark_return_pct": benchmark.benchmark_return_pct,
            "excess_return_pct": benchmark.excess_return_pct,
            "per_symbol_buy_hold": [list(row) for row in benchmark.per_symbol_buy_hold],
            "status": benchmark.status,
            "assumption": benchmark.assumption,
            "note": benchmark.note,
        }
        limitations = [
            "Sequential per-symbol execution; large universes take minutes (progress shown).",
            "Side filter is post-execution; strategy logic itself is unmodified.",
            "Volatility/trend regimes derive from trailing-20 closes at entry.",
            "Gap/opening-range/volume/sector regimes need unavailable series.",
            *(list(warnings)),
        ]
        analysis_bundle["stats"] = stats_dict
        analysis_bundle["montecarlo"] = montecarlo_dict
        analysis_bundle["benchmark"] = benchmark_dict
        analysis_bundle["robustness"] = real_sensitivity
        analysis_bundle["validation"] = validation
        report = build_report(
            {**experiment.to_dict(), "reproducibility": reproducibility},
            summary,
            {"buckets": condition_summary, "symbols": symbol_dicts, "unsupported": unsupported},
            real_sensitivity,
            validation,
            stats_dict,
            benchmark_dict,
            None,
            limitations,
        )
        experiment.analysis = analysis_bundle
        experiment.result_summary = summary
        experiment.result = {"summary": summary, "status": "COMPLETED"}
        experiment.execution_ids = (f"EXP-{experiment.experiment_id}",)
        experiment.config_fingerprint = config_fp
        experiment.result_fingerprint = result_fp
        experiment.strategy_source_hash = source_hash
        experiment.engine_source_hash = engine_hash
        experiment.dataset_version = dataset_version
        experiment.data_reference = ref
        experiment.executed_at = executed_at
        experiment.validation_status = str(validation_status)
        experiment.reproducibility = reproducibility
        experiment.report = report
        experiment.status = "COMPLETED"
        try:
            trade_dicts = [t.to_dict() if hasattr(t, "to_dict") else dict(t) for t in ordered]
            save_run_artifacts(experiment.experiment_id, signals, trade_dicts, self._data_dir)
        except Exception as exc:
            _persist("FAILED")
            message = f"EXPERIMENT FAILED — artifact persist failed: {exc}"
            _say(message)
            return experiment.to_dict(), log
        try:
            save_experiment_update(experiment, self._data_dir)
        except Exception as exc:
            message = f"EXPERIMENT FAILED — persist failed: {exc}"
            _say(message)
            return experiment.to_dict(), log
        _emit(
            "FINALIZING",
            1,
            1,
            f"Experiment completed — {len(ordered)} trades, {len(signals)} signals",
        )
        return experiment.to_dict(), log

    @staticmethod
    def _trade_field(trade: Any, key: str, default: Any = None) -> Any:
        if isinstance(trade, dict):
            return trade.get(key, default)
        return getattr(trade, key, default)

    @staticmethod
    def _dataset_version(
        timeframe: str, start: str, end: str, ref: dict[str, Any], total_bars: int
    ) -> str:
        per_symbol = ref.get("per_symbol", {}) if isinstance(ref, dict) else {}
        identity = {
            "timeframe": timeframe,
            "start": start,
            "end": end,
            "total_bars": total_bars,
            "symbols": sorted(
                (symbol, info.get("bars", 0), info.get("first", ""), info.get("last", ""))
                for symbol, info in per_symbol.items()
            ),
        }
        return _source_hash(__import__("json").dumps(identity, sort_keys=True, ensure_ascii=False))[
            :16
        ]

    @staticmethod
    def _bucket_dict(bucket: Any) -> dict[str, Any]:
        return {
            "dimension": getattr(bucket, "dimension", ""),
            "key": getattr(bucket, "key", ""),
            "n": getattr(bucket, "n", 0),
            "total_pnl": getattr(bucket, "total_pnl", 0.0),
            "win_rate": getattr(bucket, "win_rate", None),
            "profit_factor": getattr(bucket, "profit_factor", None),
            "expectancy": getattr(bucket, "expectancy", None),
            "avg_win": getattr(bucket, "avg_win", None),
            "avg_loss": getattr(bucket, "avg_loss", None),
        }

    @staticmethod
    def _symbol_dict(row: Any) -> dict[str, Any]:
        return {
            "symbol": getattr(row, "symbol", ""),
            "trades": getattr(row, "trades", 0),
            "net_pnl": getattr(row, "net_pnl", 0.0),
            "win_rate": getattr(row, "win_rate", None),
            "profit_factor": getattr(row, "profit_factor", None),
            "expectancy": getattr(row, "expectancy", None),
            "avg_trade": getattr(row, "avg_trade", None),
            "max_drawdown_abs": getattr(row, "max_drawdown_abs", None),
            "sharpe": getattr(row, "sharpe", None),
        }

    # ── experiment display bundle + comparison ────────────────

    def experiment_bundle(self, experiment_id: str) -> dict[str, Any] | None:
        """Full display bundle for one experiment (empty states stay honest)."""
        stored = self.get_experiment(str(experiment_id))
        if stored is None:
            return None
        bundle: dict[str, Any] = {"experiment": stored, "signals": [], "trades": []}
        if stored.get("status") != "COMPLETED":
            return bundle
        try:
            from strategy.research.storage import load_run_artifacts

            signals, trades = load_run_artifacts(str(experiment_id), self._data_dir)
        except Exception:
            signals, trades = [], []
        bundle["signals"] = signals
        bundle["trades"] = trades
        summary = stored.get("result_summary", {}) or {}
        bundle["summary"] = summary
        stored_analysis = stored.get("analysis", {}) or {}
        if stored_analysis:
            bundle["analysis"] = stored_analysis
            bundle["conditions"] = (stored_analysis.get("conditions", {}) or {}).get("buckets", {})
            bundle["symbol_rows"] = (stored_analysis.get("conditions", {}) or {}).get("symbols", [])
            bundle["unsupported"] = (stored_analysis.get("conditions", {}) or {}).get(
                "unsupported", []
            )
            bundle["stats"] = stored_analysis.get("stats", {})
            bundle["robustness"] = stored_analysis.get("robustness", [])
            bundle["validation"] = stored_analysis.get("validation", {})
            bundle["montecarlo"] = stored_analysis.get("montecarlo", {})
            bundle["benchmark"] = stored_analysis.get("benchmark", {})
        trade_objects: list[Any] = []
        try:
            from backtest.models.trade import TradeRecord  # pyright: ignore[reportMissingImports]

            for item in trades:
                if isinstance(item, dict):
                    try:
                        trade_objects.append(TradeRecord.from_dict(item))
                    except Exception:
                        continue
        except Exception:
            trade_objects = []
        if trade_objects and not stored_analysis:
            try:
                from strategy.research.conditions import analyze_conditions as _conditions

                conditions = _conditions(trade_objects, None)
                bundle["conditions"] = {
                    dimension: [self._bucket_dict(b) for b in buckets]
                    for dimension, buckets in conditions.buckets.items()
                }
                bundle["symbol_rows"] = [self._symbol_dict(r) for r in conditions.symbols]
                bundle["unsupported"] = list(conditions.unsupported)
            except Exception:
                pass
            try:
                from strategy.research.statistics import describe_evidence as _describe

                stats = _describe(trade_objects)
                bundle["stats"] = {
                    "n": stats.n,
                    "mean": stats.mean,
                    "ci_low": stats.ci_low,
                    "ci_high": stats.ci_high,
                    "p_value": stats.p_value,
                    "cohens_d": stats.cohens_d,
                    "status": stats.status,
                    "notes": list(stats.notes),
                }
            except Exception:
                pass
        bundle["report"] = stored.get("report") or {}
        bundle["reproducibility"] = stored.get("reproducibility") or {}
        return bundle

    def compare_pair(self, left_id: str, right_id: str) -> dict[str, Any] | None:
        """Compare two persisted experiments (None when either is missing)."""
        from strategy.research.compare import compare_experiments

        left = self.get_experiment(str(left_id))
        right = self.get_experiment(str(right_id))
        if left is None or right is None:
            return None
        result = compare_experiments(left, right)
        return {
            "left_id": result.left_id,
            "right_id": result.right_id,
            "config_differences": list(result.config_differences),
            "metric_deltas": [
                {"metric": d.metric, "left": d.left, "right": d.right, "delta": d.delta}
                for d in result.metric_deltas
            ],
            "warnings": list(result.warnings),
            "verdict": result.verdict,
            "details": dict(result.details),
        }


__all__ = ["AnalysisView", "DatasetSummary", "ResearchService"]
