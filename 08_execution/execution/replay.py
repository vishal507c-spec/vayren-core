"""Live event recorder + deterministic replay.

Record a live (or paper) session's normalized market events; replay them
through a fresh runtime to reproduce the exact decision sequence. Replay
divergence is itself a testable, reportable fact — the debugging core.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from execution.events import (
    CandleEvent,
    HeartbeatEvent,
    MarketEvent,
    OrderBookEvent,
    QuoteEvent,
    TradeEvent,
)

_EVENT_TYPES: dict[str, type[MarketEvent]] = {
    "CandleEvent": CandleEvent,
    "QuoteEvent": QuoteEvent,
    "TradeEvent": TradeEvent,
    "OrderBookEvent": OrderBookEvent,
    "HeartbeatEvent": HeartbeatEvent,
}


def _encode(event: MarketEvent) -> dict[str, Any]:
    data = asdict(event)
    data["_type"] = type(event).__name__
    return data


def _decode(data: dict[str, Any]) -> MarketEvent:
    cls = _EVENT_TYPES.get(str(data.get("_type", "")))
    if cls is None:
        raise ValueError(f"unknown recorded event type: {data.get('_type')}")
    clean = {k: v for k, v in data.items() if k != "_type"}
    if cls is OrderBookEvent:
        clean["bids"] = tuple(tuple(b) for b in clean.get("bids", ()))
        clean["asks"] = tuple(tuple(a) for a in clean.get("asks", ()))
    return cls(**clean)  # type: ignore[arg-type]


class LiveEventRecorder:
    """Append normalized market events to a JSONL tape."""

    def __init__(self, path: Path | str | None = None) -> None:
        self._path = Path(path) if path is not None else None
        self._events: list[MarketEvent] = []

    def record(self, event: MarketEvent) -> None:
        self._events.append(event)
        if self._path is not None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(_encode(event), sort_keys=True) + "\n")

    @property
    def events(self) -> tuple[MarketEvent, ...]:
        return tuple(self._events)

    @staticmethod
    def load(path: Path | str) -> tuple[MarketEvent, ...]:
        out: list[MarketEvent] = []
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    out.append(_decode(json.loads(line)))
        return tuple(out)


@dataclass(frozen=True)
class ReplayReport:
    matched: bool
    compared: int
    divergences: tuple[str, ...] = ()


def replay_and_compare(
    events: tuple[MarketEvent, ...],
    run_once: Callable[[tuple[MarketEvent, ...]], tuple[str, ...]],
) -> ReplayReport:
    """Run the same tape twice through `run_once`; decision ids must match.

    `run_once` feeds the tape through a FRESH runtime and returns the
    ordered decision identifiers (signal/intent/order ids). Determinism
    means identical outputs on identical inputs.
    """
    first = run_once(events)
    second = run_once(events)
    divergences = tuple(
        f"position {i}: {a} != {b}"
        for i, (a, b) in enumerate(zip(first, second, strict=False))
        if a != b
    )
    if len(first) != len(second):
        divergences = divergences + (f"length {len(first)} != {len(second)}",)
    return ReplayReport(matched=not divergences, compared=len(first), divergences=divergences)
