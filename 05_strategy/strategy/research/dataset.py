"""ResearchDataset — generic, references immutable execution history."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    # Annotation only (never evaluated at runtime — `from __future__ import
    # annotations` is active). Runtime access below is duck-typed, so there
    # is no runtime strategy → backtest dependency.
    from backtest.execution import ExecutionHistory


@dataclass(frozen=True)
class ResearchDataset:
    """Generic dataset for research — references executions, not copies.

    Attributes:
        strategy_id: Strategy identifier
        version_id: Version identifier
        execution_ids: Tuple of execution IDs included
        trades: Aggregated trades from histories
        signals: Aggregated signals
        parameters: Version parameters
        data_identity: Generic data identity (symbol/timeframe/range)
        metadata: Arbitrary
    """

    strategy_id: str
    version_id: str
    execution_ids: tuple[str, ...]
    trades: tuple[Any, ...] = field(default_factory=tuple)
    signals: tuple[Any, ...] = field(default_factory=tuple)
    parameters: dict[str, Any] = field(default_factory=dict)
    data_identity: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def from_histories(
        strategy_id: str,
        version_id: str,
        histories: list[ExecutionHistory],
        parameters: dict[str, Any] | None = None,
    ) -> ResearchDataset:
        execution_ids = tuple(h.snapshot.execution_id for h in histories)
        # Pull actual TradeRecord objects from execution histories.
        # Previously signals were incorrectly used as a proxy for trades.
        # Now: trades come from the backtest's completed TradeRecords.
        trades = tuple(t for h in histories for t in h.trades)
        signals = tuple(s for h in histories for s in h.signals)
        # Data identity from first history if exists
        data_identity = dict(histories[0].snapshot.data_identity) if histories else {}
        params = dict(parameters or {})
        if not params and histories:
            params = dict(histories[0].snapshot.parameters)
        return ResearchDataset(
            strategy_id=strategy_id,
            version_id=str(version_id),
            execution_ids=execution_ids,
            trades=trades,
            signals=signals,
            parameters=params,
            data_identity=data_identity,
            metadata={"execution_count": len(histories)},
        )
