"""DownloadQueue — pure in-memory queue rebuilt from every DB scan (preserved).

Only missing ranges are queued — already downloaded data is never re-fetched.
Losing the queue costs nothing: candle databases are the only source of truth.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from data.calendar import target_start_dt, today_end_dt
from data.models import DLState, SymbolInfo

log = logging.getLogger("HistDownloadEngine")


@dataclass
class DownloadJob:
    symbol: str
    interval: str
    from_dt: datetime
    to_dt: datetime
    reason: str

    @property
    def key(self) -> str:
        return f"{self.symbol}|{self.interval}"


class DownloadQueue:
    """Pure in-memory queue. Never touches disk.

    Historical Remaining Coverage Rule:
        Target Start = MAX_HISTORY_YEARS back from today.
        If the earliest DB candle is later than the target → head gap exists.
        Only missing ranges are queued.
    """

    def __init__(self) -> None:
        self._jobs: list[DownloadJob] = []

    def build_from_scan(
        self,
        infos: list[SymbolInfo],
        known_symbols: set[str] | None,
        settings,
    ) -> None:
        """Build jobs from a scan; symbols absent from ``known_symbols`` are
        skipped (provider universe filter — the provider, not the queue,
        decides which symbols resolve). ``None`` = no filter."""
        self._jobs = []
        today_end = today_end_dt()
        target = target_start_dt(settings)

        # Priority: PARTIAL first, then NOT_STARTED, skip DOWNLOAD_COMPLETE
        priority_map = {
            DLState.PARTIAL_DOWNLOAD: 1,
            DLState.NOT_STARTED: 2,
            DLState.DOWNLOAD_COMPLETE: 3,
        }
        sorted_infos = sorted(infos, key=lambda si: priority_map.get(si.state, 9))

        for si in sorted_infos:
            if si.state == DLState.DOWNLOAD_COMPLETE:
                continue

            if known_symbols is not None and si.symbol not in known_symbols:
                log.warning(f"[Queue] {si.symbol} not in provider universe — skip.")
                continue

            if si.state == DLState.NOT_STARTED:
                # Full historical range
                self._jobs.append(
                    DownloadJob(
                        symbol=si.symbol,
                        interval=si.interval,
                        from_dt=target,
                        to_dt=today_end,
                        reason="NOT_STARTED: full history",
                    )
                )

            elif si.state == DLState.PARTIAL_DOWNLOAD:
                # Head gap: target_start → earliest DB candle.
                # Skipped entirely when LISTING_START is already verified.
                if si.missing_head and not si.listing_start_verified:
                    if si.earliest:
                        self._jobs.append(
                            DownloadJob(
                                symbol=si.symbol,
                                interval=si.interval,
                                from_dt=target,
                                to_dt=si.earliest - timedelta(minutes=1),
                                reason="PARTIAL: missing head coverage",
                            )
                        )
                    else:
                        self._jobs.append(
                            DownloadJob(
                                symbol=si.symbol,
                                interval=si.interval,
                                from_dt=target,
                                to_dt=today_end,
                                reason="PARTIAL: missing head (no earliest)",
                            )
                        )

                # Tail gap: latest DB candle → today
                if si.missing_tail:
                    if si.latest:
                        self._jobs.append(
                            DownloadJob(
                                symbol=si.symbol,
                                interval=si.interval,
                                from_dt=si.latest + timedelta(minutes=1),
                                to_dt=today_end,
                                reason="PARTIAL: missing tail coverage",
                            )
                        )
                    else:
                        self._jobs.append(
                            DownloadJob(
                                symbol=si.symbol,
                                interval=si.interval,
                                from_dt=target,
                                to_dt=today_end,
                                reason="PARTIAL: missing tail (no latest)",
                            )
                        )

        log.info(f"[DownloadQueue] Built {len(self._jobs)} job(s) from scan.")

    def symbols_in_order(self) -> list[tuple[str, str]]:
        seen: list[tuple[str, str]] = []
        for job in self._jobs:
            pair = (job.symbol, job.interval)
            if pair not in seen:
                seen.append(pair)
        return seen

    def jobs_for(self, symbol: str, interval: str) -> list[DownloadJob]:
        return [j for j in self._jobs if j.symbol == symbol and j.interval == interval]

    def total(self) -> int:
        return len(self._jobs)
