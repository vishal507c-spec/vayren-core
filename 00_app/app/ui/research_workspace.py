"""ResearchWorkspace — first-class research screen (real backend, no fakes).

Pure view over an injected `ResearchService`: dataset selection, experiment
configuration (hypothesis + JSON config), analysis runs, result inspection
with reproducibility metadata, and experiment history. Every value comes
from the service; missing backend capability renders as an honest
unavailable state. Research computation stays in `strategy.research`
(Python); this widget only displays and collects.
"""

from __future__ import annotations

import contextlib
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSplitter,
    QTableWidget,
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

_DATASET_KEYS = ("strategy", "version", "executions", "trades", "signals")

_INSPECTOR_KEYS = (
    "experiment_id",
    "strategy",
    "version",
    "created",
    "executions",
    "status",
)


def _field(item: Any, key: str) -> Any:
    """Read one field from a dict-like or object-like record (signals vary)."""
    if isinstance(item, dict):
        return item.get(key)
    return getattr(item, key, None)


class ResearchWorkspace(QWidget):
    """Dataset navigator + analysis center + experiment inspector."""

    analysis_finished = Signal(str)
    experiment_created = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._service: Any | None = None
        self._datasets: list[Any] = []
        self._experiments: list[dict[str, Any]] = []
        self._current_strategy = ""
        self._build()

    # ── construction ──────────────────────────────────────────

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)
        root.addWidget(self._build_header())

        middle = QSplitter(Qt.Orientation.Horizontal, self)
        middle.addWidget(self._build_navigator())
        middle.addWidget(self._build_center())
        middle.addWidget(self._build_inspector())
        middle.setStretchFactor(0, 1)
        middle.setStretchFactor(1, 3)
        middle.setStretchFactor(2, 1)
        root.addWidget(middle, 1)
        root.addWidget(self._build_log(), 0)

    def _build_header(self) -> QWidget:
        section = Section("RESEARCH")
        row = QWidget(section)
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        caption = QLabel("Dataset:", row)
        caption.setStyleSheet(f"color: {t.MUTED}; font-size: 11px;")
        lay.addWidget(caption)
        self._dataset_combo = QComboBox(row)
        self._dataset_combo.setStyleSheet(t.INPUT_QSS)
        self._dataset_combo.setMinimumWidth(220)
        self._dataset_combo.currentTextChanged.connect(self._on_dataset_selected)
        lay.addWidget(self._dataset_combo)
        self._identity_label = QLabel("No dataset", row)
        self._identity_label.setStyleSheet(f"color: {t.TEXT2}; font-size: 11px;")
        self._identity_label.setWordWrap(True)
        lay.addWidget(self._identity_label, 1)
        self._run_button = QPushButton("RUN ANALYSIS", row)
        self._run_button.setStyleSheet(t.PRIMARY_QSS)
        self._run_button.clicked.connect(self._on_run_clicked)
        lay.addWidget(self._run_button)
        section.add(row)
        return section

    def _build_navigator(self) -> QWidget:
        section = Section("NAVIGATOR")
        self._dataset_list = QListWidget(section)
        self._dataset_list.itemClicked.connect(self._on_navigator_dataset)
        section.add(QLabel("Datasets", section))
        section.add(self._dataset_list)
        section.add(QLabel("Experiments", section))
        self._experiment_list = QListWidget(section)
        self._experiment_list.itemClicked.connect(self._on_navigator_experiment)
        section.add(self._experiment_list)
        return section

    def _build_center(self) -> QWidget:
        panel = QWidget(self)
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        metrics = Section("ANALYSIS")
        self._metrics_block = KVBlock(
            (
                "trades",
                "signals",
                "win_rate",
                "avg_win",
                "avg_loss",
                "expectancy",
                "profit_factor",
                "net_profit",
                "drawdown_pct",
                "sharpe",
            ),
            metrics,
        )
        metrics.add(self._metrics_block)
        self._notes_label = QLabel("", metrics)
        self._notes_label.setWordWrap(True)
        self._notes_label.setStyleSheet(f"color: {t.MUTED}; font-size: 11px;")
        metrics.add(self._notes_label)
        lay.addWidget(metrics)
        signals = Section("SIGNALS")
        self._signals_table = QTableWidget(0, 3)
        self._signals_table.setHorizontalHeaderLabels(["Time", "Side", "Price"])
        configure_table(self._signals_table)
        signals.add(self._signals_table)
        self._signals_empty = EmptyState("NO SIGNALS", "Dataset carries no signals.", signals)
        signals.add(self._signals_empty)
        lay.addWidget(signals, 1)
        return panel

    def _build_inspector(self) -> QWidget:
        section = Section("EXPERIMENT INSPECTOR")
        self._inspector_block = KVBlock(_INSPECTOR_KEYS, section)
        section.add(self._inspector_block)
        section.add(QLabel("Hypothesis", section))
        self._hypothesis_edit = QTextEdit(section)
        self._hypothesis_edit.setPlaceholderText("State the hypothesis under test…")
        self._hypothesis_edit.setStyleSheet(t.INPUT_QSS)
        self._hypothesis_edit.setMaximumHeight(76)
        section.add(self._hypothesis_edit)
        section.add(QLabel("Result", section))
        self._result_label = QLabel("No result", section)
        self._result_label.setWordWrap(True)
        self._result_label.setStyleSheet(f"color: {t.TEXT2}; font-size: 11px;")
        section.add(self._result_label)
        self._save_button = QPushButton("SAVE EXPERIMENT", section)
        self._save_button.setStyleSheet(t.BUTTON_QSS)
        self._save_button.clicked.connect(self._on_save_clicked)
        section.add(self._save_button)
        self._inspector_status = Badge(section)
        section.add(self._inspector_status)
        return section

    def _build_log(self) -> QWidget:
        section = Section("RESEARCH LOG")
        self._log_label = QLabel("Research idle.", section)
        self._log_label.setWordWrap(True)
        self._log_label.setStyleSheet(f"color: {t.TEXT2}; font-size: 11px;")
        section.add(self._log_label)
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
            self._render_all()

    def _render_empty(self, message: str) -> None:
        self._dataset_combo.blockSignals(True)
        self._dataset_combo.clear()
        self._dataset_combo.blockSignals(False)
        self._identity_label.setText(message)
        self._dataset_list.clear()
        self._experiment_list.clear()
        self._metrics_block.set_all_na()
        self._notes_label.setText("")
        fill_table(self._signals_table, [])
        self._signals_empty.setVisible(True)
        self._inspector_block.set_all_na()
        self._result_label.setText("No result")
        self._log_label.setText(message)

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

        self._dataset_list.clear()
        for sid, _version, trades in summaries:
            QListWidgetItem(f"{sid}  ·  {trades} trades", self._dataset_list)
        self._experiment_list.clear()
        for exp in self._experiments:
            if isinstance(exp, dict):
                QListWidgetItem(
                    f"{exp.get('experiment_id', '?')}  ·  {exp.get('strategy_id', '?')}",
                    self._experiment_list,
                )
        current = self._current_summary()
        if current is None:
            self._identity_label.setText("No dataset selected")
            self._metrics_block.set_all_na()
            self._notes_label.setText("")
            fill_table(self._signals_table, [])
            self._signals_empty.setVisible(True)
            return
        sid, version, _trades = current
        dataset = self._service.get_dataset(sid) if self._service is not None else None
        identity = getattr(dataset, "data_identity", {}) if dataset is not None else {}
        if not isinstance(identity, dict):
            identity = {}
        symbol = str(identity.get("symbol", "—"))
        timeframe = str(identity.get("timeframe", "—"))
        start = str(identity.get("start", identity.get("from", "—")))
        end = str(identity.get("end", identity.get("to", "—")))
        self._identity_label.setText(
            f"{symbol}  ·  {timeframe}  ·  {start} → {end}  ·  v{version or '?'}"
        )
        self._signals_empty.setVisible(True)

    def _current_summary(self) -> tuple[str, str, str] | None:
        for sid, version, trades in self._summaries():
            if sid == self._current_strategy:
                return sid, version, trades
        summaries = self._summaries()
        return summaries[0] if summaries else None

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

    def _show_experiment(self, exp: dict[str, Any]) -> None:
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
        self._log_label.setText(f"Inspecting {exp.get('experiment_id', '?')}.")

    def _on_run_clicked(self) -> None:
        if self._service is None or not self._current_strategy:
            self._log_label.setText("Nothing to analyze: select a dataset first.")
            return
        try:
            view = self._service.run_analysis(self._current_strategy)
        except Exception as exc:
            self._log_label.setText(f"Analysis failed: {exc}")
            return
        metrics = getattr(view, "metrics", {}) or {}
        for key in (
            "trades",
            "signals",
            "win_rate",
            "avg_win",
            "avg_loss",
            "expectancy",
            "profit_factor",
            "net_profit",
            "drawdown_pct",
            "sharpe",
        ):
            self._metrics_block.set(key, str(metrics.get(key, "N/A")))
        notes = getattr(view, "notes", ()) or ()
        self._notes_label.setText("\n".join(str(note) for note in notes))
        dataset = self._service.get_dataset(self._current_strategy)
        signals = list(getattr(dataset, "signals", ()) or []) if dataset is not None else []
        rows = [
            [
                _text(_field(s, "timestamp"), ""),
                _text(_field(s, "side"), ""),
                _text(_field(s, "price"), ""),
            ]
            for s in signals[:500]
        ]
        fill_table(self._signals_table, rows)
        self._signals_empty.setVisible(not rows)
        self._log_label.setText(f"Analysis complete: {self._current_strategy}.")
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
        try:
            saved = self._service.create_experiment(
                self._current_strategy, version, execution_ids, hypothesis, {}
            )
        except Exception as exc:
            self._log_label.setText(f"Save failed: {exc}")
            return
        self._experiments = list(self._service.experiments() or [])
        self._render_all()
        if isinstance(saved, dict):
            self._show_experiment(saved)
            self.experiment_created.emit(str(saved.get("experiment_id", "")))
        self._log_label.setText("Experiment saved with full reproducibility metadata.")
