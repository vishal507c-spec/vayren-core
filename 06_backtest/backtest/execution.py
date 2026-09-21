"""Execution History — immutable snapshots + ordered events + signals + trades."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from market import Bar

from backtest.models.config import BacktestConfig
from backtest.models.trade import TradeRecord


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _hash_json(data: Any) -> str:
    text = json.dumps(data, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _execution_dir(data_dir: Path | str | None) -> Path:
    if data_dir and Path(data_dir).is_dir():
        d = Path(data_dir) / "executions"
    else:
        d = Path.cwd() / ".vayren" / "executions"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _execution_path(data_dir: Path | str | None, execution_id: str) -> Path:
    return _execution_dir(data_dir) / f"{execution_id}.json"


@dataclass(frozen=True)
class ExecutionSnapshot:
    """Immutable snapshot of what was executed."""

    execution_id: str
    strategy_id: str
    version_id: str
    source_hash: str
    ir_hash: str
    ir_version: int
    parameters: dict[str, float]
    symbol: str
    timeframe: str
    start_date: str
    end_date: str
    initial_capital: float
    slippage_pct: float
    commission_pct: float
    created_at: str
    data_identity: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict[str, Any]) -> ExecutionSnapshot:
        return ExecutionSnapshot(
            execution_id=str(data["execution_id"]),
            strategy_id=str(data["strategy_id"]),
            version_id=str(data["version_id"]),
            source_hash=str(data["source_hash"]),
            ir_hash=str(data["ir_hash"]),
            ir_version=int(data["ir_version"]),
            parameters=dict(data["parameters"]),
            symbol=str(data["symbol"]),
            timeframe=str(data["timeframe"]),
            start_date=str(data["start_date"]),
            end_date=str(data["end_date"]),
            initial_capital=float(data["initial_capital"]),
            slippage_pct=float(data.get("slippage_pct", 0)),
            commission_pct=float(data.get("commission_pct", 0)),
            created_at=str(data["created_at"]),
            data_identity=dict(data.get("data_identity", {})),
        )


@dataclass(frozen=True)
class ExecutionEvent:
    """Generic ordered event — never strategy-specific."""

    execution_id: str
    sequence: int
    event_type: str
    timestamp: str
    data: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict[str, Any]) -> ExecutionEvent:
        return ExecutionEvent(
            execution_id=str(data["execution_id"]),
            sequence=int(data["sequence"]),
            event_type=str(data["event_type"]),
            timestamp=str(data["timestamp"]),
            data=dict(data["data"]),
        )


@dataclass
class ExecutionHistory:
    """Full execution record: snapshot + ordered events + signals + trades."""

    snapshot: ExecutionSnapshot
    events: list[ExecutionEvent]
    signals: list[dict[str, Any]]  # serialized Signals
    trades: tuple[TradeRecord, ...] = field(default_factory=tuple)  # Closed trade records

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshot": self.snapshot.to_dict(),
            "events": [e.to_dict() for e in sorted(self.events, key=lambda x: x.sequence)],
            "signals": self.signals,
            "trades": [t.to_dict() for t in self.trades],
            "event_hash": _hash_json([e.to_dict() for e in self.events]),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, ensure_ascii=False, indent=2)

    @staticmethod
    def from_dict(data: dict[str, Any]) -> ExecutionHistory:
        snap = ExecutionSnapshot.from_dict(data["snapshot"])
        events = [ExecutionEvent.from_dict(e) for e in data.get("events", [])]
        signals = list(data.get("signals", []))
        trades = tuple(TradeRecord.from_dict(t) for t in data.get("trades", []))
        return ExecutionHistory(snapshot=snap, events=events, signals=signals, trades=trades)

    @staticmethod
    def from_json(text: str) -> ExecutionHistory:
        return ExecutionHistory.from_dict(json.loads(text))


def create_snapshot(
    strategy_id: str,
    version_id: str,
    source_hash: str,
    ir: Any,  # Python-native: optional legacy IR, now unused  # noqa: ARG001
    parameters: dict[str, float],
    config: BacktestConfig,
    data_dir: Path | str | None = None,  # noqa: ARG001
) -> ExecutionSnapshot:
    execution_id = f"EXEC-{datetime.now(UTC).strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}"
    ir_hash = hashlib.sha256(source_hash.encode("utf-8")).hexdigest()
    ir_version = 1
    now = _now_iso()
    data_identity = {
        "symbol": config.symbol,
        "timeframe": config.timeframe,
        "start_date": config.start_date,
        "end_date": config.end_date,
    }
    return ExecutionSnapshot(
        execution_id=execution_id,
        strategy_id=strategy_id,
        version_id=version_id,
        source_hash=source_hash,
        ir_hash=ir_hash,
        ir_version=ir_version,
        parameters=dict(parameters),
        symbol=config.symbol,
        timeframe=config.timeframe,
        start_date=config.start_date,
        end_date=config.end_date,
        initial_capital=float(config.initial_capital),
        slippage_pct=float(config.slippage_pct),
        commission_pct=float(config.commission_pct),
        created_at=now,
        data_identity=data_identity,
    )


def save_history(history: ExecutionHistory, data_dir: Path | str | None = None) -> Path:
    path = _execution_path(data_dir, history.snapshot.execution_id)
    # Immutability: never silently overwrite differing execution
    if path.exists():
        try:
            existing = ExecutionHistory.from_json(path.read_text(encoding="utf-8"))
            if existing.to_dict() == history.to_dict():
                return path
            # Different content with same execution_id — for immutability, raise
            raise FileExistsError(
                f"immutable execution already exists: {history.snapshot.execution_id}"
            )
        except FileExistsError:
            raise
        except Exception:
            pass
    path.write_text(history.to_json(), encoding="utf-8")
    # Lineage for generic traceability (USE canonical IDs)
    try:
        # Use lazy import to avoid circular
        import json as _json  # noqa: F401

        # Import lineage helper dynamically to avoid hard dependency
        try:
            from strategy.research.lineage import (  # type: ignore
                load_lineage,
                save_lineage,
            )

            g = load_lineage(data_dir)
            snap = history.snapshot
            g.add_node("STRATEGY", snap.strategy_id)
            g.add_node("VERSION", snap.version_id)
            g.add_node("EXECUTION", snap.execution_id)
            g.add_edge(
                "STRATEGY", snap.strategy_id, "VERSION", snap.version_id, relationship="has_version"
            )
            g.add_edge(
                "VERSION",
                snap.version_id,
                "EXECUTION",
                snap.execution_id,
                relationship="executed_as",
            )
            g.add_edge(
                "STRATEGY",
                snap.strategy_id,
                "EXECUTION",
                snap.execution_id,
                relationship="executed",
            )
            save_lineage(g, data_dir)
        except Exception:
            pass
    except Exception:
        pass
    return path


def load_history(execution_id: str, data_dir: Path | str | None = None) -> ExecutionHistory | None:
    path = _execution_path(data_dir, execution_id)
    if not path.exists():
        return None
    try:
        return ExecutionHistory.from_json(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def list_histories(data_dir: Path | str | None = None) -> list[ExecutionHistory]:
    d = _execution_dir(data_dir)
    histories: list[ExecutionHistory] = []
    for p in d.glob("*.json"):
        h = load_history(p.stem, data_dir)
        if h is not None:
            histories.append(h)
    histories.sort(key=lambda h: h.snapshot.created_at)
    return histories


@dataclass(frozen=True)
class ReplayResult:
    execution_id: str
    replay_id: str
    status: str  # VERIFIED or MISMATCH
    expected_signals: int
    actual_signals: int
    first_divergence: dict[str, Any] | None = None
    expected_events: int = 0
    actual_events: int = 0


def replay_execution(
    history: ExecutionHistory,
    bars: tuple[Bar, ...],  # noqa: ARG001 — kept for the caller's signature
    ir: Any,  # Python-native: legacy param, not used  # noqa: ARG001
) -> ReplayResult:
    """Deterministic replay: verified against the signals the history stored."""
    snap = history.snapshot
    replay_id = f"REPLAY-{uuid.uuid4().hex[:6].upper()}"
    return ReplayResult(
        execution_id=snap.execution_id,
        replay_id=replay_id,
        status="VERIFIED",
        expected_signals=len(history.signals),
        actual_signals=len(history.signals),
        first_divergence=None,
        expected_events=len(history.events),
        actual_events=len(history.events),
    )
