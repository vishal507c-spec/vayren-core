"""ProviderCredentialsDialog — the in-app credential configuration modal.

Provider-agnostic: the dialog renders exactly the fields the selected
provider advertises via its credential schema (``data.provider.credentials``),
so a future broker defines its own form without touching this UI flow.
The dialog never talks to the engine, the contract or the store directly —
everything goes through the injected
:class:`data.provider.manager.ProviderCredentialsManager`.

Security behavior:
- every ``secret`` field starts masked and re-masks on focus loss;
- the eye button reveals the value only while checked;
- secret values never appear in status text, tooltips or messages;
- validation errors name the missing field label only.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from data.provider.credentials import CredentialField, ProviderConfigError
from data.provider.manager import ProviderCredentialsManager

_BODY_STYLE = "font-size: 12px;"
_CAPTION_STYLE = "color: palette(placeholder-text); font-size: 10px;"
_STATUS_STYLE = "font-size: 11px; font-weight: 600;"
_SUB_STYLE = "color: palette(placeholder-text); font-size: 10px;"


class _SecretEdit(QLineEdit):
    """Masked field that hides the value again when it loses focus."""

    reveal_changed = Signal(bool)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setEchoMode(QLineEdit.EchoMode.Password)
        self._revealed = False

    def set_revealed(self, revealed: bool) -> None:
        if revealed == self._revealed:
            return
        self._revealed = revealed
        self.setEchoMode(QLineEdit.EchoMode.Normal if revealed else QLineEdit.EchoMode.Password)
        self.reveal_changed.emit(revealed)

    def is_revealed(self) -> bool:
        return self._revealed

    def focusOutEvent(self, event) -> None:  # noqa: N802 — Qt override
        if self._revealed:
            self.set_revealed(False)
        super().focusOutEvent(event)


class ProviderCredentialsDialog(QDialog):
    """Modal form: provider fields, Test Connection, Save & Connect, Clear."""

    def __init__(self, manager: ProviderCredentialsManager, parent=None) -> None:
        super().__init__(parent)
        self._manager = manager
        self._edits: dict[str, QLineEdit] = {}
        self._eyes: dict[str, QToolButton] = {}

        self.setWindowTitle(f"Configure {manager.display_name}")
        self.setModal(True)
        self.setMinimumWidth(360)

        column = QVBoxLayout(self)
        column.setContentsMargins(16, 14, 16, 14)
        column.setSpacing(8)

        for field in manager.fields:
            column.addWidget(self._field_row(field))

        column.addSpacing(4)

        test_row = QHBoxLayout()
        test_row.setSpacing(6)
        self._test_button = QPushButton("Test Connection", self)
        self._test_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._test_button.clicked.connect(self._on_test)
        test_row.addWidget(self._test_button)
        test_row.addStretch(1)
        column.addLayout(test_row)

        self._status_line = QLabel("● Not tested", self)
        self._status_line.setStyleSheet(_STATUS_STYLE)
        column.addWidget(self._status_line)
        self._status_sub = QLabel("", self)
        self._status_sub.setStyleSheet(_SUB_STYLE)
        self._status_sub.setWordWrap(True)
        column.addWidget(self._status_sub)

        column.addSpacing(4)

        actions = QHBoxLayout()
        actions.setSpacing(6)
        self._clear_button = QPushButton("Clear Credentials", self)
        self._clear_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._clear_button.clicked.connect(self._on_clear)
        actions.addWidget(self._clear_button)
        actions.addStretch(1)
        cancel = QPushButton("Cancel", self)
        cancel.clicked.connect(self.reject)
        save = QPushButton("Save & Connect", self)
        save.setDefault(True)
        save.clicked.connect(self._on_save)
        actions.addWidget(cancel)
        actions.addWidget(save)
        column.addLayout(actions)

        self._clear_button.setVisible(manager.has_stored())

    # ── construction ─────────────────────────────────────────────────────────

    def _field_row(self, field: CredentialField) -> QWidget:
        caption = QLabel(field.label, self)
        caption.setStyleSheet(_CAPTION_STYLE)
        row = QVBoxLayout()
        row.setSpacing(2)
        row.addWidget(caption)
        box = QHBoxLayout()
        box.setSpacing(4)
        edit: QLineEdit
        if field.secret:
            edit = _SecretEdit(self)
            eye = QToolButton(self)
            eye.setText("👁")
            eye.setCheckable(True)
            eye.setToolTip("Show / hide")
            eye.setCursor(Qt.CursorShape.PointingHandCursor)
            eye.clicked.connect(lambda checked, e=edit: e.set_revealed(checked))
            edit.reveal_changed.connect(eye.setChecked)
            self._eyes[field.key] = eye
            box.addWidget(edit, 1)
            box.addWidget(eye)
        else:
            edit = QLineEdit(self)
            box.addWidget(edit, 1)
        edit.setStyleSheet(_BODY_STYLE)
        edit.setText(self._manager.load_values().get(field.key, ""))
        if field.help:
            edit.setToolTip(field.help)
        self._edits[field.key] = edit
        row.addLayout(box)
        holder = QWidget(self)
        holder.setLayout(row)
        return holder

    # ── actions ──────────────────────────────────────────────────────────────

    def _collect(self) -> dict[str, str]:
        return {key: edit.text() for key, edit in self._edits.items()}

    def _set_status(self, line: str, sub: str = "") -> None:
        self._status_line.setText(line)
        self._status_sub.setText(sub)

    def _on_test(self) -> None:
        values = self._collect()
        error = self._manager.validate(values)
        if error is not None:
            self._set_status(f"✕ {error}")
            return
        self._set_status("Testing connection…")
        self._test_button.setEnabled(False)
        try:
            ready, _reason = self._manager.test_connection(values)
        finally:
            self._test_button.setEnabled(True)
        if ready:
            self._set_status(
                "✓ Connection successful", f"{self._manager.display_name} provider is ready."
            )
        else:
            self._set_status(
                "✕ Connection failed",
                "Authentication failed.\nCheck your API credentials.",
            )

    def _on_save(self) -> None:
        values = self._collect()
        try:
            self._manager.save(values)
        except ProviderConfigError as exc:
            self._set_status(f"✕ {exc}")
            return
        self._clear_button.setVisible(False)
        self.accept()

    def _on_clear(self) -> None:
        if not self._confirm_clear():
            return
        self._manager.clear()
        for edit in self._edits.values():
            edit.clear()
            if isinstance(edit, _SecretEdit):
                edit.set_revealed(False)
        self._clear_button.setVisible(False)
        self._set_status("● Not Configured", "Locally stored credentials removed.")

    def _confirm_clear(self) -> bool:
        box = QMessageBox(self)
        box.setWindowTitle("Clear Credentials")
        box.setText("Remove saved credentials?")
        box.setInformativeText("This will delete the locally stored provider credentials.")
        remove = box.addButton("Remove", QMessageBox.ButtonRole.AcceptRole)
        cancel = box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        cancel.setDefault(True)
        box.exec()
        return box.clickedButton() is remove
