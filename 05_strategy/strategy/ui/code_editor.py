"""CodeEditor — full editor for Strategy Lab (Pine-like).

Features: line numbers, syntax highlighting, current line, error highlight,
monospace dark theme, find/replace, undo/redo, scrolling.
"""

from PySide6.QtCore import QRect, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QKeySequence,
    QPainter,
    QSyntaxHighlighter,
    QTextCharFormat,
    QTextCursor,
    QTextFormat,
)
from PySide6.QtWidgets import (
    QInputDialog,
    QPlainTextEdit,
    QTextEdit,
    QWidget,
)

# Python-native strategy editor highlight terms (DSL removed)
HIGHLIGHT_KEYWORDS = (
    "class",
    "def",
    "self",
    "if",
    "elif",
    "else",
    "for",
    "while",
    "return",
    "import",
    "from",
    "and",
    "or",
    "not",
    "in",
    "None",
    "True",
    "False",
    "lambda",
    "pass",
    "break",
    "continue",
)
HIGHLIGHT_FUNCTIONS = (
    "buy",
    "sell",
    "close_position",
    "stop_loss",
    "take_profit",
    "time_exit",
    "PythonStrategy",
    "calc_rsi",
    "calc_sma",
    "calc_ema",
    "calc_atr",
    "calc_range",
    "ParameterSpec",
    "StrategyParameters",
)


class StrategyHighlighter(QSyntaxHighlighter):
    def __init__(self, parent):
        super().__init__(parent)
        self._keyword_fmt = QTextCharFormat()
        self._keyword_fmt.setForeground(QColor("#00C7B7"))
        self._keyword_fmt.setFontWeight(QFont.Weight.Bold)
        self._func_fmt = QTextCharFormat()
        self._func_fmt.setForeground(QColor("#7BD8CC"))
        self._string_fmt = QTextCharFormat()
        self._string_fmt.setForeground(QColor("#21C58B"))
        self._comment_fmt = QTextCharFormat()
        self._comment_fmt.setForeground(QColor("#596675"))
        self._comment_fmt.setFontItalic(True)
        self._number_fmt = QTextCharFormat()
        self._number_fmt.setForeground(QColor("#DDAA45"))

    def highlightBlock(self, text: str):  # noqa: N802
        # comments
        if "#" in text:
            idx = text.find("#")
            self.setFormat(idx, len(text) - idx, self._comment_fmt)
            text = text[:idx]
        # strings
        in_str = False
        start = -1
        quote = ""
        for i, ch in enumerate(text):
            if not in_str and ch in ('"', "'"):
                in_str = True
                quote = ch
                start = i
            elif in_str and ch == quote:
                self.setFormat(start, i - start + 1, self._string_fmt)
                in_str = False
        # numbers
        import re

        for m in re.finditer(r"\b\d+(\.\d+)?\b", text):
            self.setFormat(m.start(), m.end() - m.start(), self._number_fmt)
        # keywords
        for kw in HIGHLIGHT_KEYWORDS:
            for m in re.finditer(rf"\b{kw}\b", text):
                self.setFormat(m.start(), len(kw), self._keyword_fmt)
        # functions
        for fn in HIGHLIGHT_FUNCTIONS:
            for m in re.finditer(rf"\b{fn}\b(?=\s*\()", text):
                self.setFormat(m.start(), len(fn), self._func_fmt)


class LineNumberArea(QWidget):
    def __init__(self, editor):
        super().__init__(editor)
        self._editor = editor

    def sizeHint(self):  # noqa: N802
        return QSize(self._editor.line_number_width(), 0)

    def paintEvent(self, event):  # noqa: N802
        self._editor.paint_line_numbers(event)


class CodeEditor(QPlainTextEdit):
    """QPlainTextEdit with line numbers, highlighter, error/current line."""

    find_requested = Signal(str)

    def __init__(self, parent=None):  # type: ignore[no-untyped-def]
        super().__init__(parent)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        font = QFont("JetBrains Mono", 10)
        if not font.exactMatch():
            font = QFont("Consolas", 10)
        font.setStyleHint(QFont.StyleHint.Monospace)
        font.setFixedPitch(True)
        self.setFont(font)
        # comfortable line height via stylesheet line-height not directly supported, use extra spacing via document margin  # noqa: E501
        self.setStyleSheet(
            "QPlainTextEdit { background: #0B1017; color: #E6EDF3; border: none; selection-background-color: #0E4F49; selection-color: #FFFFFF; }"  # noqa: E501
            "QPlainTextEdit:focus { border: none; }"
        )
        # tab = 4 spaces, indentation
        self.setTabStopDistance(self.fontMetrics().horizontalAdvance(" ") * 4)
        self._highlighter = StrategyHighlighter(self.document())
        self._line_area = LineNumberArea(self)
        self.blockCountChanged.connect(self._update_line_width)
        self.updateRequest.connect(self._update_line_area)
        self.cursorPositionChanged.connect(self._highlight_current)
        self._error_line: int | None = None
        self._error_col: int | None = None
        self._error_msg: str = ""
        self._update_line_width(0)
        self._highlight_current()
        # shortcuts
        self._setup_shortcuts()
        self.setPlaceholderText("Start writing your strategy...")

    def _setup_shortcuts(self):
        from PySide6.QtGui import QAction

        act = QAction(self)
        act.setShortcut(QKeySequence.StandardKey.Find)  # type: ignore[attr-defined]
        act.triggered.connect(self._find_dialog)
        self.addAction(act)
        act2 = QAction(self)
        act2.setShortcut(QKeySequence("Ctrl+H"))  # type: ignore[call-arg]
        act2.triggered.connect(self._replace_dialog)
        self.addAction(act2)

    def _find_dialog(self):
        txt, ok = QInputDialog.getText(self, "Find", "Find:")
        if ok and txt:  # noqa: SIM102
            if not self.find(txt):
                self.moveCursor(QTextCursor.MoveOperation.Start)
                self.find(txt)

    def _replace_dialog(self):
        find_txt, ok = QInputDialog.getText(self, "Replace", "Find:")
        if not ok or not find_txt:
            return
        repl_txt, ok2 = QInputDialog.getText(self, "Replace", "Replace with:")
        if not ok2:
            return
        cursor = self.textCursor()
        cursor.beginEditBlock()
        self.moveCursor(QTextCursor.MoveOperation.Start)
        while self.find(find_txt):
            self.textCursor().insertText(repl_txt)
        cursor.endEditBlock()

    def line_number_width(self) -> int:
        digits = len(str(max(1, self.blockCount())))
        return 14 + self.fontMetrics().horizontalAdvance("9") * digits

    def _update_line_width(self, _):
        self.setViewportMargins(self.line_number_width(), 0, 0, 0)

    def _update_line_area(self, rect, dy):
        if dy:
            self._line_area.scroll(0, dy)
        else:
            self._line_area.update(0, rect.y(), self._line_area.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self._update_line_width(0)

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        cr = self.contentsRect()
        self._line_area.setGeometry(
            QRect(cr.left(), cr.top(), self.line_number_width(), cr.height())
        )

    def paint_line_numbers(self, event):
        painter = QPainter(self._line_area)
        painter.fillRect(event.rect(), QColor("#070B10"))
        block = self.firstVisibleBlock()
        block_num = block.blockNumber()
        top = int(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        bottom = top + int(self.blockBoundingRect(block).height())
        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                is_error = self._error_line is not None and block_num + 1 == self._error_line
                painter.setPen(QColor("#F05A67") if is_error else QColor("#596675"))
                painter.drawText(
                    0,
                    top,
                    self._line_area.width() - 4,
                    self.fontMetrics().height(),
                    Qt.AlignmentFlag.AlignRight,
                    str(block_num + 1),
                )
                if is_error:
                    painter.setPen(QColor("#F05A67"))
                    painter.drawRect(
                        0, top, self._line_area.width() - 1, self.fontMetrics().height()
                    )
            block = block.next()
            top = bottom
            bottom = top + int(self.blockBoundingRect(block).height())
            block_num += 1

    def _highlight_current(self):
        extra = []
        if not self.isReadOnly():
            sel = QTextEdit.ExtraSelection()
            sel.format.setBackground(QColor("#131B24"))
            sel.format.setProperty(QTextFormat.Property.FullWidthSelection, True)  # type: ignore[attr-defined]
            sel.cursor = self.textCursor()
            sel.cursor.clearSelection()
            extra.append(sel)
        # error line highlight handled via line numbers + extra
        if self._error_line is not None:
            block = self.document().findBlockByNumber(self._error_line - 1)
            if block.isValid():
                sel = QTextEdit.ExtraSelection()
                sel.format.setBackground(QColor("#33161B"))
                sel.format.setProperty(QTextFormat.Property.FullWidthSelection, True)  # type: ignore[attr-defined]
                c = QTextCursor(block)
                c.movePosition(QTextCursor.MoveOperation.NextBlock, QTextCursor.MoveMode.KeepAnchor)  # type: ignore[attr-defined]
                sel.cursor = c
                extra.append(sel)
        self.setExtraSelections(extra)

    def set_error(self, line: int | None, col: int | None = None, msg: str = ""):
        self._error_line = line
        self._error_col = col
        self._error_msg = msg
        self._highlight_current()
        self._line_area.update()
        if line is not None:
            block = self.document().findBlockByNumber(line - 1)
            if block.isValid():
                cur = QTextCursor(block)
                # move to column if given
                if col is not None:
                    cur.movePosition(
                        QTextCursor.MoveOperation.Right,
                        QTextCursor.MoveMode.MoveAnchor,
                        max(0, col),
                    )  # type: ignore[attr-defined]
                self.setTextCursor(cur)
                self.centerCursor()
                self.setFocus()

    def clear_error(self):
        self.set_error(None)

    def set_code(self, code: str):
        self.setPlainText(code)
        self.clear_error()
