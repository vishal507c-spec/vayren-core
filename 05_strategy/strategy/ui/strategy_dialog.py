"""StrategyDialog — add/configure a strategy definition."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
)

from strategy.models.definition import StrategyDefinition
from strategy.models.form import StrategyDraft
from strategy.models.parameters import ParameterSpec


class StrategyDialog(QDialog):
    """Form dialog to create or reconfigure a strategy definition."""

    _SECONDARY_LABEL = "color: palette(placeholder-text); font-size: 10px;"

    def __init__(
        self,
        kinds: tuple[str, ...],
        specs_map: dict[str, tuple[ParameterSpec, ...]],
        definition: StrategyDefinition | None = None,
        parent=None,  # type: ignore[no-untyped-def]
    ) -> None:
        super().__init__(parent)
        self._specs_map = specs_map
        self._param_spins: dict[str, QDoubleSpinBox] = {}

        mode = "Configure" if definition is not None else "Add"
        self.setWindowTitle(f"{mode} Strategy")
        self.setMinimumWidth(340)

        outer = QVBoxLayout(self)
        outer.setSpacing(8)
        form = QFormLayout()
        form.setSpacing(6)

        self._name_edit = QLineEdit(self)
        self._name_edit.setPlaceholderText("e.g. My Strategy")
        if definition is not None:
            self._name_edit.setText(definition.name)
        form.addRow(self._label("Name"), self._name_edit)

        self._version_edit = QLineEdit(self)
        self._version_edit.setPlaceholderText("e.g. 1.0")
        self._version_edit.setText(definition.version if definition is not None else "1.0")
        form.addRow(self._label("Version"), self._version_edit)

        self._kind_combo = QComboBox(self)
        for kind in kinds:
            self._kind_combo.addItem(kind, userData=kind)
        if definition is not None:
            index = self._kind_combo.findData(definition.kind)
            if index >= 0:
                self._kind_combo.setCurrentIndex(index)
            self._kind_combo.setEnabled(False)
        form.addRow(self._label("Kind"), self._kind_combo)

        outer.addLayout(form)

        self._params_container = QVBoxLayout()
        self._params_container.setSpacing(4)
        outer.addLayout(self._params_container)
        self._rebuild_param_fields()

        allocation_row = QHBoxLayout()
        allocation_row.addWidget(self._label("Allocation %"))
        self._allocation_spin = QDoubleSpinBox(self)
        self._allocation_spin.setRange(0, 100)
        self._allocation_spin.setDecimals(0)
        self._allocation_spin.setSuffix("%")
        self._allocation_spin.setValue(
            definition.allocation_pct if definition is not None else 100.0
        )
        allocation_row.addWidget(self._allocation_spin)
        allocation_row.addStretch(1)
        outer.addLayout(allocation_row)

        self._kind_combo.currentTextChanged.connect(self._rebuild_param_fields)

        self._button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self
        )
        self._button_box.accepted.connect(self.accept)
        self._button_box.rejected.connect(self.reject)
        outer.addWidget(self._button_box)

        self._specs_for_params: tuple[ParameterSpec, ...] | None = None
        if definition is not None:
            self._initial_params: dict[str, float] = dict(definition.params)
        else:
            self._initial_params = {}

    def _label(self, text: str) -> QLabel:
        label = QLabel(text, self)
        label.setStyleSheet(self._SECONDARY_LABEL)
        return label

    def _rebuild_param_fields(self) -> None:
        while self._params_container.count():
            item = self._params_container.takeAt(0)
            if item is None:
                continue
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._param_spins.clear()
        kind = str(self._kind_combo.currentData() or "")
        specs = self._specs_map.get(kind, ())
        for spec in specs:
            row = QHBoxLayout()
            row.addWidget(self._label(spec.label))
            spin = QDoubleSpinBox(self)
            spin.setRange(spec.minimum, spec.maximum)
            spin.setDecimals(spec.decimals)
            default = self._initial_params.get(spec.key, spec.default)
            spin.setValue(float(default))
            spin.setFixedWidth(120)
            row.addWidget(spin)
            row.addStretch(1)
            container = self._params_container
            row_widget = self._row_widget(row)
            container.addWidget(row_widget)
            self._param_spins[spec.key] = spin

    def _row_widget(self, layout):  # type: ignore[no-untyped-def]
        from PySide6.QtWidgets import QWidget

        wrapper = QWidget(self)
        wrapper.setLayout(layout)
        return wrapper

    def draft(self) -> StrategyDraft | None:
        """Validated draft from the dialog, or None when invalid."""
        name = self._name_edit.text().strip()
        version = self._version_edit.text().strip() or "1.0"
        kind = str(self._kind_combo.currentData() or "")
        if not name or not kind:
            return None
        params = {key: float(spin.value()) for key, spin in self._param_spins.items()}
        return StrategyDraft(
            name=name,
            version=version,
            kind=kind,
            params=params,
            allocation_pct=float(self._allocation_spin.value()),
        )
