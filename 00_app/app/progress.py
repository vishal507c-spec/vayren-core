"""Live progress for a long universe run — real state, never a fake timer.

The rule this module exists to enforce: **every number on screen is measured**.
There is no ticker, no interpolation and no "assume it finishes evenly" — a
progress event is emitted from inside the execution loop, after real work
completed, and the ETA is the mean of the per-symbol durations actually
observed so far.

Three consequences worth stating, because they are what make the numbers
trustworthy:

* **ETA needs samples.** Until ``MIN_ETA_SAMPLES`` symbols are done there is no
  rate and the ETA is ``None`` — the UI shows a dash rather than a guess.
* **A slow symbol is not a dead one.** ``long_running`` is true while the
  in-flight symbol has spent longer than ``LONG_RUNNING_SECS``; progress still
  does not advance, which is exactly what separates "slow" from "stuck".
* **Cancellation is cooperative.** It is checked between symbols, so a cancel
  can never leave half-written results behind.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

#: Samples required before an ETA is published. Three is the smallest number
#: that is not dominated by a single unusually fast or slow symbol.
MIN_ETA_SAMPLES = 3
#: A symbol in flight for longer than this is flagged LONG-RUNNING for the UI.
LONG_RUNNING_SECS = 20.0
#: The UI is told the run is quiet after this long with no completed symbol.
HEARTBEAT_WARN_SECS = 30.0


class RunProgress:
    """Accumulates real execution facts and emits them as flat dicts.

    Pure bookkeeping: it holds no results and changes no trading decision, so
    turning it off (``emit=None``) cannot change a single number of the run.
    """

    def __init__(
        self,
        total: int,
        emit: Callable[[dict], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> None:
        self.total = max(0, int(total))
        self._emit = emit
        self._should_cancel = should_cancel
        self.started = time.monotonic()
        self.completed = 0
        self.failed: list[str] = []
        self.skipped: list[str] = []
        self.retrying = 0
        self.current = ""
        self.stage = "starting"
        self.stage_pct = 0.0
        self.trades = 0
        self.bars = 0
        self.net_pnl = 0.0
        self.cancelled = False
        self._started = False
        self._durations: list[float] = []
        self._symbol_started = 0.0
        self._last_emit = 0.0
        # Load-stage bookkeeping: the read of 500+ symbols is the longest
        # silent stretch, so it carries its own real counters.
        self.loaded = 0
        self.load_total = 0
        self._load_mean: float | None = None
        self._load_eta: float | None = None
        self._load_elapsed = 0.0

    # ── queries the loop asks ──────────────────────────────────────────
    def cancel_requested(self) -> bool:
        """True once the user asked to stop. Checked BETWEEN symbols only."""
        if self._should_cancel is None:
            return False
        if self._should_cancel():
            self.cancelled = True
            return True
        return False

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.started

    @property
    def current_elapsed(self) -> float:
        """Seconds spent on the in-flight symbol (0 when idle)."""
        return time.monotonic() - self._symbol_started if self.current else 0.0

    @property
    def mean_seconds(self) -> float | None:
        """Mean seconds per COMPLETED symbol, or None while still sampling."""
        if len(self._durations) < MIN_ETA_SAMPLES:
            return None
        return sum(self._durations) / len(self._durations)

    @property
    def eta_seconds(self) -> float | None:
        """ETA from measured work only. ``None`` until the rate exists.

        During the load stage the measured work is symbols READ, not symbols
        run — the screen shows the load counter, never a run ETA.
        """
        if self.stage == "data" and self.loaded:
            return self._load_eta
        mean = self.mean_seconds
        if mean is None:
            return None
        return mean * (self.total - self.completed - len(self.failed) - len(self.skipped))

    @property
    def elapsed_total(self) -> float:
        """Wall time since the run began, whichever stage is active."""
        return self._load_elapsed if self.stage == "data" and self._load_elapsed else self.elapsed

    @property
    def headline(self) -> tuple[int, int]:
        """`(done, total)` for the stage on screen, so the bar never lies.

        The load stage counts symbols READ; the run stage counts every symbol
        that left the loop — completed, failed or skipped — because a failed
        symbol is finished work, not work still to do.
        """
        if self.stage == "data" and self.load_total:
            return self.loaded, self.load_total
        return self.completed + len(self.failed) + len(self.skipped), self.total

    @property
    def headline_pct(self) -> float:
        done, total = self.headline
        if total <= 0:
            return 0.0
        return round(done * 100.0 / total, 1)

    @property
    def remaining(self) -> int:
        return max(0, self.total - self.completed - len(self.failed) - len(self.skipped))

    @property
    def throughput(self) -> float:
        """Completed symbols per second over the whole run (0 while idle)."""
        done = self.completed + len(self.failed) + len(self.skipped)
        if done == 0:
            return 0.0
        return done / max(self.elapsed, 1e-9)

    @property
    def long_running(self) -> bool:
        return bool(self.current) and self.current_elapsed >= LONG_RUNNING_SECS

    @property
    def quiet(self) -> bool:
        """True when work is in flight but nothing finished for a while."""
        return self.current_elapsed >= HEARTBEAT_WARN_SECS

    @property
    def pct(self) -> float:
        done, total = self.headline
        if total <= 0:
            return 0.0
        return round(done * 100.0 / total, 1)

    # ── the event the UI renders ───────────────────────────────────────
    def snapshot(self) -> dict[str, Any]:
        done, total = self.headline
        mean = self._load_mean if (self.stage == "data" and self._load_mean) else self.mean_seconds
        return {
            "stage": self.stage,
            "stage_pct": round(self.stage_pct, 1),
            "total": self.total,
            "loaded": self.loaded,
            "done": done,
            "headline_total": total,
            "completed": self.completed,
            "failed": list(self.failed),
            "skipped": list(self.skipped),
            "retrying": self.retrying,
            "remaining": self.remaining,
            "pct": self.pct,
            "current": self.current,
            "current_secs": round(self.current_elapsed, 1),
            "elapsed_secs": round(self.elapsed_total, 1),
            "eta_secs": None if self.eta_seconds is None else round(self.eta_seconds, 1),
            "mean_secs": None if mean is None else round(mean, 3),
            "throughput": round(self.throughput, 3),
            "trades": self.trades,
            "bars": self.bars,
            "net_pnl": round(self.net_pnl, 2),
            "long_running": self.long_running,
            "quiet": self.quiet,
            "cancelled": self.cancelled,
        }

    def publish(self, force: bool = False) -> None:
        """Emit the current state. Throttled so a fast run cannot flood stdout."""
        if self._emit is None:
            return
        now = time.monotonic()
        if not force and now - self._last_emit < 0.1:
            return
        self._last_emit = now
        self._emit(self.snapshot())

    # ── what the execution loop calls ──────────────────────────────────
    def symbol_started(self, symbol: str, stage: str = "bars") -> None:
        self.current = symbol
        self.stage = stage
        self.stage_pct = 0.0
        self._symbol_started = time.monotonic()
        self._started = True
        self.publish(force=True)

    def symbol_progress(self, stage: str, stage_pct: float) -> None:
        """Sub-stage of the in-flight symbol (load → calculate → trades)."""
        self.stage = stage
        self.stage_pct = max(0.0, min(100.0, stage_pct))
        self.publish()

    def symbol_done(
        self,
        trades: list[dict],
        bars: int,
        pnl: float,
    ) -> None:
        self.completed += 1
        self.trades += len(trades)
        self.bars += bars
        self.net_pnl += pnl
        if self._started:
            self._durations.append(time.monotonic() - self._symbol_started)
        self.current = ""
        self.stage_pct = 100.0
        self.publish(force=True)

    def symbol_failed(self, symbol: str) -> None:
        self.failed.append(symbol)
        if self._started:
            self._durations.append(time.monotonic() - self._symbol_started)
        self.current = ""
        self.publish(force=True)

    def symbol_skipped(self, symbol: str) -> None:
        self.skipped.append(symbol)
        if self._started:
            self._durations.append(time.monotonic() - self._symbol_started)
        self.current = ""
        self.publish(force=True)

    def load_progress(
        self,
        done: int,
        total: int,
        symbol: str,
        mean_secs: float | None,
        eta_secs: float | None,
        elapsed_secs: float,
    ) -> None:
        """One symbol's bars landed during the (long) load stage.

        Reported with its own count so the screen never sits on `0 / 527`
        while a third of the run is already done.
        """
        self.loaded = done
        self.load_total = total
        self.stage = "data"
        self.current = symbol
        self.stage_pct = 0.0 if total <= 0 else round(done * 100.0 / total, 1)
        self._load_mean = mean_secs
        self._load_eta = eta_secs
        self._load_elapsed = elapsed_secs
        self.publish(force=True)

    def set_stage(self, stage: str, stage_pct: float = 0.0) -> None:
        self.stage = stage
        self.stage_pct = stage_pct
        self.publish(force=True)
