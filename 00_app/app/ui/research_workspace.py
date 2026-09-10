"""ResearchWorkspace — institutional quantitative research laboratory.

Pure view over an injected `ResearchService`. The workstation answers
WHAT (hypothesis) → WHICH DATA → WHICH STRATEGY → WHICH EXPERIMENT →
WHAT DID IT FIND → WHY → CAN IT BE REPRODUCED → SHOULD IT VALIDATE.

Every value comes from the service / `strategy.research` engine. Anything
the engine has not supplied renders as N/A / NOT RUN / READY — never a
fabricated number, trade, experiment or timestamp. The log records only
actions that really happened in this session.
"""

from __future__ import annotations

import contextlib
from datetime import datetime
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app.ui import lab_theme as t
from app.ui.ui_kit import (
    Badge,
    EmptyState,
    KVBlock,
    Section,
    configure_table,
    fill_table,
)
from app.ui.ui_kit import text as _text

_METRIC_KEYS = (
    "total_trades",
    "signals",
    "win_rate",
    "avg_win",
    "avg_loss",
    "expectancy",
    "profit_factor",
    "net_profit",
    "max_drawdown",
    "drawdown_pct",
    "sharpe",
    "sortino",
    "cagr",
    "volatility",
    "payoff_ratio",
    "exposure",
    "turnover",
)

# Legacy key aliases kept so existing callers/tests keep working: the block
# carries "trades" (== total_trades) and "drawdown_abs" (== max_drawdown).
_METRIC_BLOCK_KEYS = (
    "trades",
    "signals",
    "win_rate",
    "avg_win",
    "avg_loss",
    "expectancy",
    "profit_factor",
    "net_profit",
    "max_drawdown",
    "drawdown_abs",
    "drawdown_pct",
    "sharpe",
    "sortino",
    "cagr",
    "volatility",
    "payoff_ratio",
    "exposure",
    "turnover",
)

_INSPECTOR_KEYS = (
    "experiment_id",
    "strategy",
    "version",
    "created",
    "executions",
    "status",
)

_CONFIG_KEYS = ("strategy", "version", "timeframe", "parameters")

_DATA_KEYS = ("universe", "symbol", "period", "executions")

_VALIDATION_KEYS = ("status", "summary")

_REPRO_KEYS = (
    "experiment_id",
    "strategy_version",
    "dataset",
    "dataset_version",
    "parameters",
    "timeframe",
    "date_range",
)

_QUALITY_KEYS = (
    "bars",
    "symbols",
    "date_range",
    "missing_bars",
    "duplicate_bars",
    "timezone",
    "data_status",
)

_TRADE_DETAIL_KEYS = (
    "entry_time",
    "entry_price",
    "exit_time",
    "exit_price",
    "direction",
    "exit_reason",
    "pnl",
    "holding",
    "mfe",
    "mae",
)

_SIGNAL_COLUMNS = ("TIME", "SYMBOL", "TF", "SIDE", "PRICE", "EVENT", "STRATEGY", "EXP")

_TRADE_COLUMNS = ("#", "ENTRY", "EXIT", "SIDE", "QTY", "P&L", "REASON", "HOLD")

_ROBUSTNESS_COLUMNS = ("TEST", "INPUT", "STABILITY", "EVIDENCE")

_COMPARISON_COLUMNS = ("EXPERIMENT", "STRATEGY", "STATUS", "DETAIL")

_STATUS_TONES = {
    "READY": "muted",
    "RUNNING": "warn",
    "COMPLETE": "ok",
    "FAILED": "bad",
    "NO DATA": "muted",
    "NO TRADES": "warn",
    "INVALID EXPERIMENT": "bad",
    "NOT RUN": "muted",
    "HAS RESULT": "accent",
    "NO RESULT": "muted",
    "RECORDED": "accent",
    "DRAFT": "muted",
}


def _field(item: Any, key: str) -> Any:
    """Read one field from a dict-like or object-like record (signals vary)."""
    if isinstance(item, dict):
        return item.get(key)
    return getattr(item, key, None)


def _first_text(item: Any, keys: tuple[str, ...], default: str = "N/A") -> str:
    for key in keys:
        value = _field(item, key)
        if value is not None and str(value) != "":
            return str(value)
    return default


class ResearchWorkspace(QWidget):
    """Dataset navigator + hypothesis-first analysis center + inspector."""

    analysis_finished = Signal(str)
    experiment_created = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._service: Any | None = None
        self._datasets: list[Any] = []
        self._experiments: list[dict[str, Any]] = []
        self._current_strategy = ""
        self._current_experiment_id = ""
        self._status = "READY"
        self._log_lines: list[str] = []
        self._last_robustness: list[dict[str, Any]] = []
        self._last_validation: dict[str, Any] = {"status": "NOT RUN", "summary": "Not run."}
        self._build()

    # ── construction ──────────────────────────────────────────

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)
        root.addWidget(self._build_command_bar())

        middle = QSplitter(Qt.Orientation.Horizontal, self)
        middle.addWidget(self._build_navigator())
        middle.addWidget(self._build_center())
        middle.addWidget(self._build_inspector())
        middle.setStretchFactor(0, 1)
        middle.setStretchFactor(1, 3)
        middle.setStretchFactor(2, 1)
        root.addWidget(middle, 1)
        root.addWidget(self._build_log(), 0)

    def _build_command_bar(self) -> QWidget:
        section = Section("RESEARCH COMMAND")
        top = QWidget(section)
        lay = QHBoxLayout(top)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        caption = QLabel("Dataset:", top)
        caption.setStyleSheet(f"color: {t.MUTED}; font-size: 11px;")
        lay.addWidget(caption)
        self._dataset_combo = QComboBox(top)
        self._dataset_combo.setStyleSheet(t.INPUT_QSS)
        self._dataset_combo.setMinimumWidth(180)
        self._dataset_combo.currentTextChanged.connect(self._on_dataset_selected)
        lay.addWidget(self._dataset_combo)
        self._identity_label = QLabel("No dataset", top)
        self._identity_label.setStyleSheet(f"color: {t.TEXT2}; font-size: 11px;")
        self._identity_label.setWordWrap(True)
        lay.addWidget(self._identity_label, 1)
        self._status_badge = Badge(top)
        self._status_badge.set_status("READY", "muted")
        lay.addWidget(self._status_badge)
        self._run_button = QPushButton("RUN ANALYSIS", top)
        self._run_button.setStyleSheet(t.PRIMARY_QSS)
        self._run_button.clicked.connect(self._on_run_clicked)
        lay.addWidget(self._run_button)
        section.add(top)

        bottom = QWidget(section)
        blay = QHBoxLayout(bottom)
        blay.setContentsMargins(0, 0, 0, 0)
        blay.setSpacing(6)
        exp_caption = QLabel("Experiment:", bottom)
        exp_caption.setStyleSheet(f"color: {t.MUTED}; font-size: 11px;")
        blay.addWidget(exp_caption)
        self._experiment_combo = QComboBox(bottom)
        self._experiment_combo.setStyleSheet(t.INPUT_QSS)
        self._experiment_combo.setMinimumWidth(160)
        self._experiment_combo.currentTextChanged.connect(self._on_experiment_combo)
        blay.addWidget(self._experiment_combo)
        self._cmd_detail_label = QLabel("N/A", bottom)
        self._cmd_detail_label.setStyleSheet(f"color: {t.MUTED}; font-size: 11px;")
        self._cmd_detail_label.setWordWrap(True)
        blay.addWidget(self._cmd_detail_label, 1)
        section.add(bottom)
        return section

    def _build_navigator(self) -> QWidget:
        section = Section("NAVIGATOR")
        self._datasets_caption = QLabel("Datasets", section)
        section.add(self._datasets_caption)
        self._dataset_list = QListWidget(section)
        self._dataset_list.itemClicked.connect(self._on_navigator_dataset)
        section.add(self._dataset_list)
        self._experiments_caption = QLabel("Experiments", section)
        section.add(self._experiments_caption)
        self._experiment_list = QListWidget(section)
        self._experiment_list.itemClicked.connect(self._on_navigator_experiment)
        section.add(self._experiment_list)
        self._navigator_hint = QLabel("Select a dataset, then run.", section)
        self._navigator_hint.setWordWrap(True)
        self._navigator_hint.setStyleSheet(f"color: {t.MUTED}; font-size: 11px;")
        section.add(self._navigator_hint)
        return section

    def _build_center(self) -> QWidget:
        panel = QWidget(self)
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        hypothesis = Section("HYPOTHESIS")
        self._hypothesis_edit = QTextEdit(hypothesis)
        self._hypothesis_edit.setPlaceholderText("State the hypothesis under test…")
        self._hypothesis_edit.setStyleSheet(t.INPUT_QSS)
        self._hypothesis_edit.setMaximumHeight(64)
        hypothesis.add(self._hypothesis_edit)
        self._hypothesis_hint = QLabel("Hypothesis → experiment → analysis → evidence.", hypothesis)
        self._hypothesis_hint.setWordWrap(True)
        self._hypothesis_hint.setStyleSheet(f"color: {t.MUTED}; font-size: 11px;")
        hypothesis.add(self._hypothesis_hint)
        lay.addWidget(hypothesis)

        metrics = Section("ANALYSIS RESULTS")
        self._metrics_block = KVBlock(_METRIC_BLOCK_KEYS, metrics)
        metrics.add(self._metrics_block)
        self._notes_label = QLabel("", metrics)
        self._notes_label.setWordWrap(True)
        self._notes_label.setStyleSheet(f"color: {t.MUTED}; font-size: 11px;")
        metrics.add(self._notes_label)
        lay.addWidget(metrics)

        tabs = QTabWidget(panel)
        tabs.setStyleSheet(
            f"QTabWidget::pane {{ border: 1px solid {t.BORDER}; }}"
            f"QTabBar::tab {{ background: {t.PANEL}; color: {t.TEXT2};"
            " padding: 4px 10px; font-size: 11px; }"
            f"QTabBar::tab:selected {{ color: {t.TEXT}; background: {t.PANEL2}; }}"
        )
        self._center_tabs = tabs
        tabs.addTab(self._build_signals_tab(), "SIGNALS")
        tabs.addTab(self._build_trades_tab(), "TRADES")
        tabs.addTab(self._build_robustness_tab(), "ROBUSTNESS")
        tabs.addTab(self._build_comparison_tab(), "COMPARE")
        tabs.addTab(self._build_quality_tab(), "DATA")
        tabs.addTab(self._build_visuals_tab(), "VISUALS")
        lay.addWidget(tabs, 1)
        return panel

    def _build_signals_tab(self) -> QWidget:
        panel = QWidget(self)
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        self._signal_filter = QLineEdit(panel)
        self._signal_filter.setPlaceholderText("Filter signals…")
        self._signal_filter.setStyleSheet(t.INPUT_QSS)
        self._signal_filter.textChanged.connect(self._apply_signal_filter)
        lay.addWidget(self._signal_filter)
        self._signals_table = QTableWidget(0, len(_SIGNAL_COLUMNS))
        self._signals_table.setHorizontalHeaderLabels(list(_SIGNAL_COLUMNS))
        self._signals_table.setSortingEnabled(True)
        configure_table(self._signals_table)
        self._signals_table.itemSelectionChanged.connect(self._on_signal_selected)
        lay.addWidget(self._signals_table, 1)
        self._signals_empty = EmptyState("NO SIGNALS", "No signals in dataset.", panel)
        lay.addWidget(self._signals_empty)
        self._signal_detail = QLabel("Select a signal to inspect.", panel)
        self._signal_detail.setWordWrap(True)
        self._signal_detail.setStyleSheet(f"color: {t.MUTED}; font-size: 11px;")
        lay.addWidget(self._signal_detail)
        return panel

    def _build_trades_tab(self) -> QWidget:
        panel = QWidget(self)
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        self._trade_filter = QLineEdit(panel)
        self._trade_filter.setPlaceholderText("Filter trades…")
        self._trade_filter.setStyleSheet(t.INPUT_QSS)
        self._trade_filter.textChanged.connect(self._apply_trade_filter)
        lay.addWidget(self._trade_filter)
        self._trades_table = QTableWidget(0, len(_TRADE_COLUMNS))
        self._trades_table.setHorizontalHeaderLabels(list(_TRADE_COLUMNS))
        self._trades_table.setSortingEnabled(True)
        configure_table(self._trades_table)
        self._trades_table.itemSelectionChanged.connect(self._on_trade_selected)
        lay.addWidget(self._trades_table, 1)
        self._trades_empty = EmptyState("NO TRADES", "No closed trades in dataset.", panel)
        lay.addWidget(self._trades_empty)
        detail_section = Section("TRADE INSPECTOR")
        self._trade_detail_block = KVBlock(_TRADE_DETAIL_KEYS, detail_section)
        detail_section.add(self._trade_detail_block)
        lay.addWidget(detail_section)
        return panel

    def _build_robustness_tab(self) -> QWidget:
        panel = QWidget(self)
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        self._robustness_table = QTableWidget(0, len(_ROBUSTNESS_COLUMNS))
        self._robustness_table.setHorizontalHeaderLabels(list(_ROBUSTNESS_COLUMNS))
        self._robustness_table.setSortingEnabled(True)
        configure_table(self._robustness_table)
        lay.addWidget(self._robustness_table, 1)
        self._robustness_empty = EmptyState(
            "NOT RUN", "Robustness runs with the analysis engine.", panel
        )
        lay.addWidget(self._robustness_empty)
        self._param_hint = QLabel(
            "Modes: single run · sweep · grid · range · walk-forward · OOS · sensitivity.",
            panel,
        )
        self._param_hint.setWordWrap(True)
        self._param_hint.setStyleSheet(f"color: {t.MUTED}; font-size: 11px;")
        lay.addWidget(self._param_hint)
        return panel

    def _build_comparison_tab(self) -> QWidget:
        panel = QWidget(self)
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        self._comparison_table = QTableWidget(0, len(_COMPARISON_COLUMNS))
        self._comparison_table.setHorizontalHeaderLabels(list(_COMPARISON_COLUMNS))
        self._comparison_table.setSortingEnabled(True)
        configure_table(self._comparison_table)
        lay.addWidget(self._comparison_table, 1)
        self._comparison_empty = EmptyState(
            "NO EXPERIMENTS", "Save experiments to compare them here.", panel
        )
        lay.addWidget(self._comparison_empty)
        return panel

    def _build_quality_tab(self) -> QWidget:
        panel = QWidget(self)
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        quality = Section("DATA QUALITY")
        self._quality_block = KVBlock(_QUALITY_KEYS, quality)
        quality.add(self._quality_block)
        lay.addWidget(quality)
        self._quality_hint = QLabel(
            "Values come from the engine; unchecked stays NOT CHECKED.", panel
        )
        self._quality_hint.setWordWrap(True)
        self._quality_hint.setStyleSheet(f"color: {t.MUTED}; font-size: 11px;")
        lay.addWidget(self._quality_hint)
        lay.addStretch(1)
        return panel

    def _build_visuals_tab(self) -> QWidget:
        panel = QWidget(self)
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        self._visuals_empty = EmptyState(
            "READY",
            "Price / equity / drawdown / distribution charts appear here once "
            "the engine supplies a dated equity curve. No synthetic charts.",
            panel,
        )
        lay.addWidget(self._visuals_empty)
        lay.addStretch(1)
        return panel

    def _build_inspector(self) -> QWidget:
        section = Section("EXPERIMENT INSPECTOR")
        self._inspector_block = KVBlock(_INSPECTOR_KEYS, section)
        section.add(self._inspector_block)
        section.add(QLabel("Configuration", section))
        self._config_block = KVBlock(_CONFIG_KEYS, section)
        section.add(self._config_block)
        section.add(QLabel("Data", section))
        self._data_block = KVBlock(_DATA_KEYS, section)
        section.add(self._data_block)
        section.add(QLabel("Result", section))
        self._result_label = QLabel("No result", section)
        self._result_label.setWordWrap(True)
        self._result_label.setStyleSheet(f"color: {t.TEXT2}; font-size: 11px;")
        section.add(self._result_label)
        section.add(QLabel("Validation", section))
        self._validation_block = KVBlock(_VALIDATION_KEYS, section)
        section.add(self._validation_block)
        section.add(QLabel("Reproducibility", section))
        self._repro_block = KVBlock(_REPRO_KEYS, section)
        section.add(self._repro_block)
        self._save_button = QPushButton("SAVE EXPERIMENT", section)
        self._save_button.setStyleSheet(t.BUTTON_QSS)
        self._save_button.clicked.connect(self._on_save_clicked)
        section.add(self._save_button)
        self._inspector_status = Badge(section)
        self._inspector_status.set_status("READY", "muted")
        section.add(self._inspector_status)
        return section

    def _build_log(self) -> QWidget:
        section = Section("RESEARCH LOG / ENGINE STATUS")
        top = QWidget(section)
        lay = QHBoxLayout(top)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self._engine_badge = Badge(top)
        self._engine_badge.set_status("READY", "muted")
        lay.addWidget(self._engine_badge)
        self._log_label = QLabel("READY — No experiment executed.", section)
        self._log_label.setWordWrap(True)
        self._log_label.setStyleSheet(f"color: {t.TEXT2}; font-size: 11px;")
        lay.addWidget(self._log_label, 1)
        section.add(top)
        self._log_view = QTextEdit(section)
        self._log_view.setReadOnly(True)
        self._log_view.setStyleSheet(t.INPUT_QSS)
        self._log_view.setMaximumHeight(84)
        self._log_view.setPlaceholderText("Engine events appear here.")
        section.add(self._log_view)
        return section

    # ── service binding ───────────────────────────────────────

    def set_service(self, service: Any | None) -> None:
        self._service = service
        self.refresh()

    def refresh(self) -> None:
        """Pull datasets + experiments from the service. Never raises."""
        with contextlib.suppress(Exception):
            if self._service is None:
                self._render_empty("No research service attached.")
                return
            self._datasets = list(self._service.refresh_datasets() or [])
            self._experiments = list(self._service.experiments() or [])
            if self._datasets and self._current_strategy not in {
                str(getattr(d, "strategy_id", d)) for d in self._datasets
            }:
                first = self._datasets[0]
                self._current_strategy = str(getattr(first, "strategy_id", first))
            self._log("Datasets refreshed.")
            self._render_all()

    def _render_empty(self, message: str) -> None:
        self._set_status("READY")
        self._dataset_combo.blockSignals(True)
        self._dataset_combo.clear()
        self._dataset_combo.blockSignals(False)
        self._experiment_combo.blockSignals(True)
        self._experiment_combo.clear()
        self._experiment_combo.blockSignals(False)
        self._identity_label.setText(message)
        self._cmd_detail_label.setText("N/A")
        self._dataset_list.clear()
        self._experiment_list.clear()
        self._datasets_caption.setText("Datasets")
        self._experiments_caption.setText("Experiments")
        self._metrics_block.set_all_na()
        self._notes_label.setText("")
        fill_table(self._signals_table, [])
        self._signals_empty.setVisible(True)
        fill_table(self._trades_table, [])
        self._trades_empty.setVisible(True)
        self._trade_detail_block.set_all_na()
        fill_table(self._robustness_table, [])
        self._robustness_empty.setVisible(True)
        fill_table(self._comparison_table, [])
        self._comparison_empty.setVisible(True)
        self._quality_block.set_all_na()
        self._inspector_block.set_all_na()
        self._config_block.set_all_na()
        self._data_block.set_all_na()
        self._result_label.setText("No result")
        self._validation_block.set_all_na()
        self._repro_block.set_all_na()
        self._log_label.setText(message)
        self._log(message)

    # ── rendering ─────────────────────────────────────────────

    def _summaries(self) -> list[tuple[str, str, str]]:
        out = []
        for dataset in self._datasets:
            sid = str(getattr(dataset, "strategy_id", dataset))
            version = str(getattr(dataset, "version_id", ""))
            trades = str(getattr(dataset, "trade_count", "?"))
            out.append((sid, version, trades))
        return out

    def _render_all(self) -> None:
        summaries = self._summaries()
        self._dataset_combo.blockSignals(True)
        self._dataset_combo.clear()
        for sid, _version, _trades in summaries:
            self._dataset_combo.addItem(sid)
        if self._current_strategy:
            index = self._dataset_combo.findText(self._current_strategy)
            if index >= 0:
                self._dataset_combo.setCurrentIndex(index)
        self._dataset_combo.blockSignals(False)

        self._experiment_combo.blockSignals(True)
        self._experiment_combo.clear()
        for exp in self._experiments:
            if isinstance(exp, dict):
                self._experiment_combo.addItem(str(exp.get("experiment_id", "?")))
        if self._current_experiment_id:
            index = self._experiment_combo.findText(self._current_experiment_id)
            if index >= 0:
                self._experiment_combo.setCurrentIndex(index)
        self._experiment_combo.blockSignals(False)

        self._dataset_list.clear()
        for sid, _version, trades in summaries:
            QListWidgetItem(f"{sid}  ·  {trades} trades", self._dataset_list)
        self._datasets_caption.setText(f"Datasets ({len(summaries)})")
        self._experiment_list.clear()
        for exp in self._experiments:
            if isinstance(exp, dict):
                QListWidgetItem(
                    f"{exp.get('experiment_id', '?')}  ·  {exp.get('strategy_id', '?')}",
                    self._experiment_list,
                )
        self._experiments_caption.setText(f"Experiments ({len(self._experiments)})")
        current = self._current_summary()
        if current is None:
            self._identity_label.setText("No dataset selected")
            self._cmd_detail_label.setText("N/A")
            self._set_status("READY")
            self._metrics_block.set_all_na()
            self._notes_label.setText(
                "RESEARCH READY — No experiment has been executed. "
                "Select a dataset, define a hypothesis, then run."
            )
            fill_table(self._signals_table, [])
            self._signals_empty.setVisible(True)
            fill_table(self._trades_table, [])
            self._trades_empty.setVisible(True)
            self._render_comparison()
            self._quality_block.set_all_na()
            self._log_label.setText("READY — No experiment executed.")
            return
        sid, version, _trades = current
        dataset = self._service.get_dataset(sid) if self._service is not None else None
        identity = getattr(dataset, "data_identity", {}) if dataset is not None else {}
        if not isinstance(identity, dict):
            identity = {}
        symbol = str(identity.get("symbol", "—"))
        timeframe = str(identity.get("timeframe", "—"))
        start = str(identity.get("start_date", identity.get("start", identity.get("from", "—"))))
        end = str(identity.get("end_date", identity.get("end", identity.get("to", "—"))))
        universe = str(identity.get("universe", "NSE"))
        self._identity_label.setText(
            f"{symbol}  ·  {timeframe}  ·  {start} → {end}  ·  v{version or '?'}"
        )
        self._cmd_detail_label.setText(
            f"UNIVERSE {universe} · STRATEGY {sid} · VERSION v{version or '?'}"
            f" · TF {timeframe} · PERIOD {start} → {end}"
        )
        self._set_status("READY")
        self._metrics_block.set_all_na()
        self._trade_detail_block.set_all_na()
        self._signals_empty.setVisible(True)
        self._render_comparison()
        self._render_quality()
        self._render_inspector_context()

    def _current_summary(self) -> tuple[str, str, str] | None:
        for sid, version, trades in self._summaries():
            if sid == self._current_strategy:
                return sid, version, trades
        summaries = self._summaries()
        return summaries[0] if summaries else None

    # ── status + log (only real session events) ───────────────

    def _set_status(self, status: str) -> None:
        self._status = status
        tone = _STATUS_TONES.get(status, "muted")
        with contextlib.suppress(Exception):
            self._status_badge.set_status(status, tone)
        with contextlib.suppress(Exception):
            self._engine_badge.set_status(status, tone)

    def _log(self, message: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        line = f"[{stamp}] {message}"
        self._log_lines.append(line)
        with contextlib.suppress(Exception):
            self._log_view.setPlainText("\n".join(self._log_lines[-200:]))

    # ── interaction ───────────────────────────────────────────

    def _on_dataset_selected(self, name: str) -> None:
        if name:
            self._current_strategy = name
            self._render_all()

    def _on_navigator_dataset(self, item: QListWidgetItem) -> None:
        name = item.text().split("  ·  ")[0]
        index = self._dataset_combo.findText(name)
        if index >= 0:
            self._dataset_combo.setCurrentIndex(index)

    def _on_navigator_experiment(self, item: QListWidgetItem) -> None:
        exp_id = item.text().split("  ·  ")[0]
        for exp in self._experiments:
            if isinstance(exp, dict) and str(exp.get("experiment_id")) == exp_id:
                self._show_experiment(exp)
                return

    def _on_experiment_combo(self, exp_id: str) -> None:
        if not exp_id:
            return
        for exp in self._experiments:
            if isinstance(exp, dict) and str(exp.get("experiment_id")) == exp_id:
                self._show_experiment(exp)
                return

    def _show_experiment(self, exp: dict[str, Any]) -> None:
        self._current_experiment_id = str(exp.get("experiment_id", ""))
        self._inspector_block.set("experiment_id", _text(exp.get("experiment_id")))
        self._inspector_block.set("strategy", _text(exp.get("strategy_id")))
        self._inspector_block.set("version", _text(exp.get("version_id")))
        self._inspector_block.set("created", _text(exp.get("created_at")))
        executions = exp.get("execution_ids") or []
        self._inspector_block.set(
            "executions",
            str(len(executions)) if isinstance(executions, list) else _text(executions),
        )
        result = exp.get("result")
        self._inspector_block.set("status", "HAS RESULT" if result else "NO RESULT")
        hypothesis = exp.get("hypothesis")
        if isinstance(hypothesis, dict):
            self._hypothesis_edit.setPlainText(str(hypothesis.get("text", "")))
        result_text = str(result) if result else "No result recorded."
        self._result_label.setText(result_text)
        self._inspector_status.set_status(
            "RECORDED" if result else "DRAFT", "accent" if result else "muted"
        )
        configuration = (
            exp.get("configuration") if isinstance(exp.get("configuration"), dict) else {}
        )
        assert isinstance(configuration, dict)
        self._config_block.set("strategy", _text(exp.get("strategy_id")))
        self._config_block.set("version", _text(exp.get("version_id")))
        self._config_block.set("timeframe", _text(configuration.get("timeframe")))
        self._config_block.set("parameters", _text(configuration.get("parameters")))
        self._data_block.set("universe", _text(configuration.get("universe", "NSE")))
        self._data_block.set("symbol", _text(configuration.get("symbol")))
        self._data_block.set("period", _text(configuration.get("period")))
        self._data_block.set(
            "executions",
            str(len(executions)) if isinstance(executions, list) else _text(executions),
        )
        self._render_repro()
        self._log_label.setText(f"Inspecting {exp.get('experiment_id', '?')}.")
        self._log(f"Inspecting {exp.get('experiment_id', '?')}.")

    def _on_run_clicked(self) -> None:
        if self._service is None or not self._current_strategy:
            self._log_label.setText("Nothing to analyze: select a dataset first.")
            return
        dataset = self._service.get_dataset(self._current_strategy)
        trade_count = len(getattr(dataset, "trades", ()) or ()) if dataset is not None else 0
        signal_count = len(getattr(dataset, "signals", ()) or ()) if dataset is not None else 0
        if trade_count == 0 and signal_count == 0:
            self._set_status("NO DATA")
            self._log_label.setText(
                "Import required: dataset has no trades or signals. "
                "Run a backtest first to generate execution history."
            )
            self._log("NO DATA: run blocked — no trades or signals in dataset.")
            return
        if trade_count == 0:
            self._set_status("NO TRADES")
            self._log_label.setText(
                "No closed trades: dataset has signals but no completed trades. "
                "Run a backtest to completion before analyzing."
            )
            self._log("NO TRADES: run blocked — signals present but no closed trades.")
            return
        self._set_status("RUNNING")
        self._log_label.setText(f"Analysis running: {self._current_strategy}.")
        self._log(f"Analysis running: {self._current_strategy}.")
        try:
            view = self._service.run_analysis(self._current_strategy)
        except Exception as exc:
            self._set_status("FAILED")
            self._log_label.setText(f"Analysis failed: {exc}")
            self._log(f"Analysis failed: {exc}")
            return
        status = str(getattr(view, "status", "COMPLETE") or "COMPLETE")
        self._set_status(status)
        metrics = getattr(view, "metrics", {}) or {}
        self._metrics_block.set(
            "trades", str(metrics.get("trades", metrics.get("total_trades", "N/A")))
        )
        self._metrics_block.set("signals", str(metrics.get("signals", "N/A")))
        for key in (
            "win_rate",
            "avg_win",
            "avg_loss",
            "expectancy",
            "profit_factor",
            "net_profit",
            "drawdown_pct",
            "sharpe",
            "sortino",
            "cagr",
            "volatility",
            "payoff_ratio",
            "exposure",
            "turnover",
        ):
            self._metrics_block.set(key, str(metrics.get(key, "N/A")))
        self._metrics_block.set("max_drawdown", str(metrics.get("max_drawdown", "N/A")))
        self._metrics_block.set("drawdown_abs", str(metrics.get("drawdown_abs", "N/A")))
        notes = getattr(view, "notes", ()) or ()
        self._notes_label.setText("\n".join(str(note) for note in notes))
        self._render_signals()
        self._render_trades()
        self._render_robustness()
        self._render_validation()
        self._render_comparison()
        self._render_quality()
        self._render_inspector_context()
        if status == "COMPLETE":
            self._log_label.setText(f"Analysis complete: {self._current_strategy}.")
            self._log(f"Analysis complete: {self._current_strategy}.")
        elif status in ("NO DATA", "NO TRADES"):
            self._log_label.setText(f"{status}: {self._current_strategy} — nothing to analyze.")
            self._log(f"{status}: {self._current_strategy}.")
        else:
            self._log_label.setText(f"Analysis {status}: {self._current_strategy}.")
            self._log(f"Analysis {status}: {self._current_strategy}.")
        self.analysis_finished.emit(self._current_strategy)

    def _on_save_clicked(self) -> None:
        if self._service is None or not self._current_strategy:
            self._log_label.setText("Nothing to save: select a dataset first.")
            return
        hypothesis = self._hypothesis_edit.toPlainText().strip()
        if not hypothesis:
            self._log_label.setText("Write a hypothesis before saving the experiment.")
            return
        dataset = self._service.get_dataset(self._current_strategy)
        execution_ids = (
            list(getattr(dataset, "execution_ids", ()) or []) if dataset is not None else []
        )
        version = str(getattr(dataset, "version_id", "") or "") if dataset is not None else ""
        identity = dict(getattr(dataset, "data_identity", {}) or {}) if dataset is not None else {}
        configuration = {
            "timeframe": str(identity.get("timeframe", "") or ""),
            "symbol": str(identity.get("symbol", "") or ""),
            "universe": str(identity.get("universe", "NSE") or "NSE"),
            "parameters": dict(getattr(dataset, "parameters", {}) or {}),
        }
        try:
            saved = self._service.create_experiment(
                self._current_strategy, version, execution_ids, hypothesis, configuration
            )
        except Exception as exc:
            self._log_label.setText(f"Save failed: {exc}")
            self._log(f"Save failed: {exc}")
            return
        self._experiments = list(self._service.experiments() or [])
        self._render_all()
        if isinstance(saved, dict):
            self._show_experiment(saved)
            self.experiment_created.emit(str(saved.get("experiment_id", "")))
        self._log_label.setText("Experiment saved with full reproducibility metadata.")
        self._log("Experiment saved with reproducibility metadata.")

    # ── center tabs ───────────────────────────────────────────

    def _dataset_identity(self) -> dict[str, Any]:
        if self._service is None or not self._current_strategy:
            return {}
        dataset = self._service.get_dataset(self._current_strategy)
        identity = getattr(dataset, "data_identity", {}) if dataset is not None else {}
        return dict(identity) if isinstance(identity, dict) else {}

    def _render_signals(self) -> None:
        if self._service is None or not self._current_strategy:
            fill_table(self._signals_table, [])
            self._signals_empty.setVisible(True)
            return
        identity = self._dataset_identity()
        symbol = str(identity.get("symbol", ""))
        timeframe = str(identity.get("timeframe", ""))
        try:
            signals = list(self._service.list_signals(self._current_strategy))
        except Exception:
            signals = []
        rows = []
        for item in signals[:500]:
            time = _first_text(item, ("timestamp", "time", "entry_time"), "")
            side = _first_text(item, ("side", "kind"), "")
            if hasattr(_field(item, "kind"), "value"):
                with contextlib.suppress(Exception):
                    kind = _field(item, "kind")
                    side = str(kind.value) if kind is not None else side
            price = _first_text(item, ("price", "entry_price", "close"), "")
            event = _first_text(item, ("event", "event_type", "kind", "exit_reason"), "SIGNAL")
            strategy = _first_text(item, ("strategy_id", "strategy"), self._current_strategy)
            exp = _first_text(item, ("execution_id", "experiment_id"), "N/A")
            rows.append(
                [time, symbol or "N/A", timeframe or "N/A", side, price, event, strategy, exp]
            )
        self._signals_table.setSortingEnabled(False)
        fill_table(self._signals_table, rows)
        self._signals_table.setSortingEnabled(True)
        self._signals_empty.setVisible(not rows)
        if not rows:
            self._signal_detail.setText("No signals — dataset carries no signal events.")
        self._apply_signal_filter(self._signal_filter.text())

    def _apply_signal_filter(self, needle: str) -> None:
        query = needle.strip().lower()
        for row in range(self._signals_table.rowCount()):
            show = True
            if query:
                show = False
                for col in range(self._signals_table.columnCount()):
                    cell = self._signals_table.item(row, col)
                    if cell is not None and query in cell.text().lower():
                        show = True
                        break
            self._signals_table.setRowHidden(row, not show)

    def _on_signal_selected(self) -> None:
        selected = self._signals_table.selectedItems()
        if not selected:
            return
        row = selected[0].row()
        cells: list[str] = []
        for col in range(self._signals_table.columnCount()):
            cell = self._signals_table.item(row, col)
            cells.append(cell.text() if cell is not None else "N/A")
        self._signal_detail.setText(" · ".join(cells))

    def _render_trades(self) -> None:
        if self._service is None or not self._current_strategy:
            fill_table(self._trades_table, [])
            self._trades_empty.setVisible(True)
            return
        try:
            trades = list(self._service.list_trades(self._current_strategy))
        except Exception:
            trades = []
        rows = []
        for index, item in enumerate(trades[:500], start=1):
            entry = _first_text(item, ("entry_time",), "")
            exit_ = _first_text(item, ("exit_time",), "")
            side = _first_text(item, ("side", "direction"), "")
            qty = _first_text(item, ("quantity", "qty"), "")
            pnl = _first_text(item, ("pnl",), "")
            reason = _first_text(item, ("exit_reason", "reason"), "")
            hold = _first_text(item, ("bars_held", "holding"), "")
            rows.append([str(index), entry, exit_, side, qty, pnl, reason, hold])
        self._trades_table.setSortingEnabled(False)
        fill_table(self._trades_table, rows)
        self._trades_table.setSortingEnabled(True)
        self._trades_empty.setVisible(not rows)
        if not rows:
            self._trade_detail_block.set_all_na()
        self._apply_trade_filter(self._trade_filter.text())

    def _apply_trade_filter(self, needle: str) -> None:
        query = needle.strip().lower()
        for row in range(self._trades_table.rowCount()):
            show = True
            if query:
                show = False
                for col in range(self._trades_table.columnCount()):
                    cell = self._trades_table.item(row, col)
                    if cell is not None and query in cell.text().lower():
                        show = True
                        break
            self._trades_table.setRowHidden(row, not show)

    def _on_trade_selected(self) -> None:
        selected = self._trades_table.selectedItems()
        if not selected or self._service is None or not self._current_strategy:
            return
        row = selected[0].row()
        try:
            trades = list(self._service.list_trades(self._current_strategy))
        except Exception:
            trades = []
        if row < 0 or row >= len(trades):
            # Sorted view: fall back to visible row cells (honest subset).
            cells: list[str] = []
            for col in range(self._trades_table.columnCount()):
                cell = self._trades_table.item(row, col)
                cells.append(cell.text() if cell is not None else "N/A")
            self._trade_detail_block.set("entry_time", cells[1] if len(cells) > 1 else "N/A")
            self._trade_detail_block.set("exit_time", cells[2] if len(cells) > 2 else "N/A")
            self._trade_detail_block.set("direction", cells[3] if len(cells) > 3 else "N/A")
            self._trade_detail_block.set("pnl", cells[5] if len(cells) > 5 else "N/A")
            self._trade_detail_block.set("exit_reason", cells[6] if len(cells) > 6 else "N/A")
            self._trade_detail_block.set("holding", cells[7] if len(cells) > 7 else "N/A")
            self._trade_detail_block.set("entry_price", "N/A")
            self._trade_detail_block.set("exit_price", "N/A")
            self._trade_detail_block.set("mfe", "N/A")
            self._trade_detail_block.set("mae", "N/A")
            return
        item = trades[row]
        self._trade_detail_block.set("entry_time", _first_text(item, ("entry_time",)))
        self._trade_detail_block.set("entry_price", _first_text(item, ("entry_price",)))
        self._trade_detail_block.set("exit_time", _first_text(item, ("exit_time",)))
        self._trade_detail_block.set("exit_price", _first_text(item, ("exit_price",)))
        self._trade_detail_block.set("direction", _first_text(item, ("side", "direction")))
        self._trade_detail_block.set("exit_reason", _first_text(item, ("exit_reason",)))
        self._trade_detail_block.set("pnl", _first_text(item, ("pnl",)))
        self._trade_detail_block.set("holding", _first_text(item, ("bars_held", "holding")))
        self._trade_detail_block.set("mfe", "N/A")
        self._trade_detail_block.set("mae", "N/A")

    def _render_robustness(self) -> None:
        if self._service is None or not self._current_strategy:
            self._last_robustness = []
            fill_table(self._robustness_table, [])
            self._robustness_empty.setVisible(True)
            return
        try:
            self._last_robustness = list(self._service.run_robustness(self._current_strategy))
        except Exception:
            self._last_robustness = []
        rows = [
            [
                str(item.get("test_type", "?")),
                _text(item.get("input")),
                str(item.get("stability", "WARNING")),
                _text(item.get("evidence")),
            ]
            for item in self._last_robustness
        ]
        self._robustness_table.setSortingEnabled(False)
        fill_table(self._robustness_table, rows)
        self._robustness_table.setSortingEnabled(True)
        self._robustness_empty.setVisible(not rows)
        if not rows:
            self._robustness_empty.set_detail("NOT RUN — dataset has no parameters to vary.")

    def _render_validation(self) -> None:
        if self._service is None or not self._current_strategy:
            self._last_validation = {"status": "NOT RUN", "summary": "Not run."}
            self._validation_block.set_all_na()
            return
        try:
            self._last_validation = dict(self._service.run_validation(self._current_strategy))
        except Exception:
            self._last_validation = {"status": "FAILED", "summary": "Validation failed."}
        self._validation_block.set("status", _text(self._last_validation.get("status")))
        self._validation_block.set("summary", _text(self._last_validation.get("summary")))

    def _render_comparison(self) -> None:
        if self._service is None:
            fill_table(self._comparison_table, [])
            self._comparison_empty.setVisible(True)
            return
        try:
            rows_data = list(self._service.comparison())
        except Exception:
            rows_data = []
        rows = [
            [
                str(item.get("experiment_id", "N/A")),
                str(item.get("strategy_id", "N/A")),
                str(item.get("status", "NO RESULT")),
                "HAS RESULT" if item.get("result") else "NO RESULT",
            ]
            for item in rows_data
        ]
        self._comparison_table.setSortingEnabled(False)
        fill_table(self._comparison_table, rows)
        self._comparison_table.setSortingEnabled(True)
        self._comparison_empty.setVisible(not rows)

    def _render_quality(self) -> None:
        if self._service is None or not self._current_strategy:
            self._quality_block.set_all_na()
            return
        try:
            quality = dict(self._service.data_quality(self._current_strategy))
        except Exception:
            quality = {}
        for key in _QUALITY_KEYS:
            self._quality_block.set(key, _text(quality.get(key)))

    def _render_repro(self) -> None:
        if self._service is None or not self._current_strategy:
            self._repro_block.set_all_na()
            return
        try:
            repro = dict(
                self._service.reproducibility(
                    self._current_strategy, self._current_experiment_id or None
                )
            )
        except Exception:
            repro = {}
        for key in _REPRO_KEYS:
            self._repro_block.set(key, _text(repro.get(key)))

    def _render_inspector_context(self) -> None:
        if self._service is None or not self._current_strategy:
            self._config_block.set_all_na()
            self._data_block.set_all_na()
            self._validation_block.set("status", "NOT RUN")
            self._validation_block.set("summary", "Not run.")
            self._repro_block.set_all_na()
            return
        dataset = self._service.get_dataset(self._current_strategy)
        identity = dict(getattr(dataset, "data_identity", {}) or {}) if dataset is not None else {}
        params = dict(getattr(dataset, "parameters", {}) or {}) if dataset is not None else {}
        timeframe = str(identity.get("timeframe", "") or "")
        symbol = str(identity.get("symbol", "") or "")
        start = str(
            identity.get("start_date", identity.get("start", identity.get("from", ""))) or ""
        )
        end = str(identity.get("end_date", identity.get("end", identity.get("to", ""))) or "")
        executions = (
            list(getattr(dataset, "execution_ids", ()) or []) if dataset is not None else []
        )
        version = str(getattr(dataset, "version_id", "") or "") if dataset is not None else ""
        self._config_block.set("strategy", _text(self._current_strategy))
        self._config_block.set("version", _text(version))
        self._config_block.set("timeframe", _text(timeframe))
        self._config_block.set("parameters", _text(params) if params else "N/A")
        self._data_block.set("universe", _text(identity.get("universe", "NSE")))
        self._data_block.set("symbol", _text(symbol))
        self._data_block.set("period", f"{start} → {end}" if start or end else "N/A")
        self._data_block.set("executions", str(len(executions)))
        self._render_validation()
        self._render_repro()
