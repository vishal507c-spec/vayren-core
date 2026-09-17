"""Strategy plot ingestion — live indicator path (OBR-shaped strategies).

OBR emits ONLY universal PlotEvents (plot_ray / plot_marker: REF HIGH/LOW,
BUY / SELL / EOD / NO-TRADE) and no plot() series at all. The live
indicator path (``_run_strategy_plots``) must still ingest those events so
the chart and the Slint Market view render the full OBR overlay. This locks
that contract: no early return on an empty chart-series map.
"""

from __future__ import annotations

import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from datetime import UTC, datetime, timedelta  # noqa: E402
from pathlib import Path  # noqa: E402

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.bootstrap.bootstrap import Bootstrap  # noqa: E402

# OBR-shaped strategy: emits ONLY universal plot events, never plot().
# Mirrors the canonical OBR record's _plot_obr_visuals output shape.
_OBR_SHAPED_CODE = """\
from strategy.models.plot_event import MarkerType
from strategy.strategies.base import PythonStrategy


class Strategy(PythonStrategy):
    def warmup(self) -> int:
        return 0

    def on_bar_logic(self, view):
        # REF HIGH / REF LOW rays (extend exactly extendBars candles)
        self.plot_ray(view.index, 100.0, plot_id="ref_high", extend_bars=5)
        self.plot_ray(view.index, 99.0, plot_id="ref_low", extend_bars=5)
        # BUY marker with an owner-supplied label
        self.plot_marker(
            view.index, 100.5, MarkerType.UP_ARROW,
            plot_id="buy_%d" % view.index, text="BUY 100.50",
        )
        # EOD marker with a P&L label
        self.plot_marker(
            view.index, 101.0, MarkerType.TRIANGLE_BLUE,
            plot_id="eod_%d" % view.index, text="EOD 101.00 +0.50",
        )
"""

_BAR_COUNT = 30  # > default warmup so on_bar_logic actually runs


def _seed_strategy_record(strat_dir: Path) -> str:
    """Write the record in the production format (a JSON ``.py`` file)."""
    now = datetime.now(UTC).isoformat()
    record = {
        "id": "obrshaped-parity",
        "name": "ObrShaped",
        "code": _OBR_SHAPED_CODE,
        "created_at": now,
        "updated_at": now,
        "version": "1.0",
    }
    (strat_dir / "ObrShaped.py").write_text(json.dumps(record, indent=2), encoding="utf-8")
    return "ObrShaped"


def _synthetic_bars() -> tuple:
    from market.models.bar import Bar

    start = datetime(2026, 1, 1, 9, 15, 0)
    return tuple(
        Bar(
            symbol="SYNTH",
            open=99.0,
            high=101.5,
            low=98.5,
            close=100.5,
            volume=1000,
            timestamp=(start + timedelta(minutes=15 * i)).isoformat(sep=" "),
        )
        for i in range(_BAR_COUNT)
    )


def test_run_strategy_plots_ingests_plot_events_without_chart_series(
    qt_app: QApplication, tmp_path: Path
) -> None:
    """A strategy emitting only PlotEvents (OBR shape) still populates the
    overlay store on the live indicator path — the plot-events ingestion must
    run even when ``get_chart_series_with_owner()`` is empty."""
    assert qt_app is not None
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    strat_dir = tmp_path / "strategies"
    strat_dir.mkdir(parents=True)
    name = _seed_strategy_record(strat_dir)

    bootstrap = Bootstrap(data_dir=data_dir, limit=None, strategy_dir=strat_dir)
    try:
        overlay = bootstrap._plot_overlay
        assert overlay is not None
        bars = _synthetic_bars()
        bootstrap._run_strategy_plots(name, bars)

        # The strategy emits NO plot() series, so the chart-series map is
        # empty — but the universal plot events must still land in the store.
        assert not any(overlay._series.values()), "strategy emits no plot() series"
        total = overlay.total_plot_count()
        assert total > 0, "OBR-shaped plot events were not ingested on the live path"
        records = overlay._store.query_visible(0, len(bars))
        kinds = {r.plot_type for r in records}
        assert "RAY" in kinds, "REF HIGH/LOW rays missing"
        markers = [r for r in records if r.plot_type == "MARKER"]
        marker_kinds = {str(r.marker_type) for r in markers}
        assert "UP_ARROW" in marker_kinds, "BUY markers missing"
        assert "TRIANGLE_BLUE" in marker_kinds, "EOD markers missing"
        eod = next(r for r in markers if str(r.marker_type) == "TRIANGLE_BLUE")
        assert eod.text == "EOD 101.00 +0.50"
        ref = next(r for r in records if r.plot_id == "ref_high")
        assert ref.extend_bars == 5
    finally:
        bootstrap.stop()
