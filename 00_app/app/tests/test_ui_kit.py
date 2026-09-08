"""UiKit shared-widget contracts: structure, tones, wrapping, table rules."""

import pytest
from PySide6.QtWidgets import QTableWidget

from app.ui.ui_kit import (
    Badge,
    EmptyState,
    GateRow,
    KVBlock,
    Section,
    configure_table,
    fill_table,
    money,
    text,
)


def test_text_and_money_are_honest(qt_app) -> None:
    assert qt_app is not None
    assert text(None) == "N/A"
    assert text(True) == "YES" and text(False) == "NO"
    assert text(3.14159) == "3.14"
    assert money(None) == "N/A"
    assert money("bogus") == "N/A"
    assert money(0) == "+0.00"
    assert money(-5) == "-5.00"


def test_badge_tones(qt_app) -> None:
    assert qt_app is not None
    badge = Badge()
    badge.set_status("READY", "ok")
    assert badge.text() == "READY"
    badge.set_status("X", "nope")
    assert badge.text() == "X"


def test_kv_block_wraps_and_defaults(qt_app) -> None:
    assert qt_app is not None
    block = KVBlock(("alpha", "beta"))
    assert block.value("alpha") is not None
    assert block.value("alpha").text() == "N/A"
    assert block.value("alpha").wordWrap() is True
    block.set("alpha", "hello")
    assert block.value("alpha").text() == "hello"
    block.set_all_na()
    assert block.value("alpha").text() == "N/A"
    with pytest.raises(KeyError):
        block.value("missing")


def test_gate_row_reason_visible_and_tooltip_kept(qt_app) -> None:
    assert qt_app is not None
    row = GateRow("CREDENTIALS_READY")
    row.show()
    row.render_gate("NOT READY", "missing account_id")
    assert row.pill.text() == "NOT READY"
    assert row.name_label.toolTip() == "missing account_id"
    assert row.reason_label.text() == "missing account_id"
    assert row.reason_label.isVisible() is True
    row.render_gate("READY", "")
    assert row.reason_label.text() == ""
    assert row.reason_label.isVisible() is False


def test_configure_table_conventions(qt_app) -> None:
    assert qt_app is not None
    table = QTableWidget(0, 3)
    table.setHorizontalHeaderLabels(["A", "B", "C"])
    configure_table(table)
    assert table.alternatingRowColors() is True
    assert table.verticalHeader().defaultSectionSize() == 22
    assert table.editTriggers() == QTableWidget.EditTrigger.NoEditTriggers


def test_fill_table_skips_unchanged(qt_app) -> None:
    assert qt_app is not None
    table = QTableWidget(0, 2)
    table.setHorizontalHeaderLabels(["A", "B"])
    configure_table(table)
    assert fill_table(table, [["x", "y"]]) is True
    assert table.rowCount() == 1
    assert fill_table(table, [["x", "y"]]) is False
    assert fill_table(table, [["x", "z"]]) is True
    item = table.item(0, 1)
    assert item is not None and item.text() == "z"
    assert fill_table(table, []) is True
    assert table.rowCount() == 0


def test_section_and_empty_state(qt_app) -> None:
    assert qt_app is not None
    section = Section("TITLE")
    assert section.body is not None
    empty = EmptyState("NO DATA", "detail here")
    assert "NO DATA" in empty.text()
    assert "detail" in empty.text()
    empty.set_detail("other")
    assert "other" in empty.text()
