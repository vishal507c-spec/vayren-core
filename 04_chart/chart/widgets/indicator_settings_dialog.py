"""IndicatorSettingsDialog — real parameter editor for one chart indicator.

Opens from the indicator toolbar's settings action. Edits the indicator's
stored parameters (the same dict the chart persists and the plot runner merges
over the strategy's own defaults). The indicator's calculation logic is never
modified — this dialog only captures values and hands them back; the caller
decides what to do with them (store, persist, re-run plots).

Parameter specs for well-known indicators live in :data:`PARAM_SPECS` and are
the single source of truth — the native Slint settings popup renders the SAME
specs through the market snapshot, so both surfaces stay identical.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)


@dataclass(frozen=True)
class ParamSpec:
    """One editable parameter (presentation metadata only)."""

    key: str
    label: str
    default: float
    minimum: float = 0.0
    maximum: float = 1_000_000.0
    decimals: int = 2
    single_step: float = 0.1


# Well-known indicator parameter specs. Defaults mirror the strategies' own
# declared defaults (e.g. OBR c1_thresh=1.25 / rsi_thr=65); the plot runner
# merges stored values over the compiled defaults, so a stale entry here can
# never silently change a calculation — only an explicitly edited value does.
PARAM_SPECS: dict[str, tuple[ParamSpec, ...]] = {
    "OBR": (
        ParamSpec("c1_thresh", "C1 Threshold", 1.25, 0.5, 5.0, 2, 0.05),
        ParamSpec("rsi_thr", "RSI Threshold", 65.0, 40.0, 90.0, 0, 1.0),
        ParamSpec("exit_hour", "Exit Hour", 15.0, 0.0, 23.0, 0, 1.0),
        ParamSpec("exit_min", "Exit Minute", 15.0, 0.0, 59.0, 0, 1.0),
    ),
    "Stochastic": (
        ParamSpec("k", "%K Period", 14.0, 1.0, 100.0, 0, 1.0),
        ParamSpec("d", "%D Period", 3.0, 1.0, 100.0, 0, 1.0),
        ParamSpec("smooth", "Smoothing", 3.0, 1.0, 50.0, 0, 1.0),
    ),
    "SMA": (ParamSpec("period", "Period", 14.0, 1.0, 500.0, 0, 1.0),),
    "EMA": (ParamSpec("period", "Period", 14.0, 1.0, 500.0, 0, 1.0),),
    "RSI": (ParamSpec("period", "Period", 14.0, 1.0, 100.0, 0, 1.0),),
    "Bollinger Bands": (
        ParamSpec("period", "Period", 20.0, 2.0, 200.0, 0, 1.0),
        ParamSpec("std", "Std Dev", 2.0, 0.5, 5.0, 2, 0.1),
    ),
    "ATR": (ParamSpec("period", "Period", 14.0, 1.0, 200.0, 0, 1.0),),
    "ADX": (ParamSpec("period", "Period", 14.0, 1.0, 200.0, 0, 1.0),),
    "OBV": (),
    "VWAP": (),
    "Supertrend": (
        ParamSpec("period", "Period", 10.0, 1.0, 200.0, 0, 1.0),
        ParamSpec("multiplier", "Multiplier", 3.0, 0.5, 10.0, 2, 0.1),
    ),
}


def param_specs_for(name: str) -> tuple[ParamSpec, ...]:
    """Specs for a known indicator, or generic rows for stored custom keys."""
    specs = PARAM_SPECS.get(name)
    if specs is not None:
        return specs
    return ()


def param_rows_for(name: str, stored: dict[str, float] | None) -> tuple[dict, ...]:
    """Snapshot rows for the native settings popup: one dict per parameter.

    Values merge stored overrides over spec defaults (and over any stored keys
    without a spec, presented generically). Pure data — no Qt here, so the
    market snapshot can call it for the Slint surface.
    """
    out: list[dict] = []
    specs = param_specs_for(name)
    seen: set[str] = set()
    stored = stored or {}
    for spec in specs:
        seen.add(spec.key)
        try:
            value = float(stored.get(spec.key, spec.default))
        except (TypeError, ValueError):
            value = spec.default
        out.append(
            {
                "key": spec.key,
                "label": spec.label,
                "value": value,
                "min": spec.minimum,
                "max": spec.maximum,
                "step": spec.single_step,
                "decimals": spec.decimals,
            }
        )
    # stored keys this indicator's specs do not describe (custom indicators)
    for key, value in sorted(stored.items()):
        if key in seen:
            continue
        try:
            num = float(value)
        except (TypeError, ValueError):
            continue
        out.append(
            {
                "key": str(key),
                "label": str(key),
                "value": num,
                "min": -1_000_000.0,
                "max": 1_000_000.0,
                "step": 0.1,
                "decimals": 2,
            }
        )
    return tuple(out)


class _SettingsForm:
    """Holds the editable fields of a settings dialog (no Qt subclass)."""

    def __init__(self) -> None:
        self.spins: dict[str, QDoubleSpinBox] = {}

    def read(self) -> dict[str, float]:
        return {key: float(spin.value()) for key, spin in self.spins.items()}


def build_indicator_settings_dialog(
    name: str,
    stored: dict[str, float] | None = None,
    parent: QWidget | None = None,
) -> QDialog:
    """Build (not exec) the compact parameter editor for one indicator.

    A plain :class:`QDialog` is assembled — no new Qt widget subclass — so
    the retained-Qt fallback surface stays within the migration gate while
    the canonical surface remains the native Slint popup. The caller execs
    the dialog and reads values via :func:`read_indicator_settings`.
    """
    specs = param_specs_for(name)
    form = _SettingsForm()
    dialog = QDialog(parent)
    dialog.setWindowTitle(f"{name} — Settings")
    dialog.setMinimumWidth(300)

    outer = QVBoxLayout(dialog)
    outer.setSpacing(8)

    title = QLabel(f"{name}", dialog)
    f = QFont(title.font())
    f.setWeight(QFont.Weight.DemiBold)
    title.setFont(f)
    outer.addWidget(title)

    stored = dict(stored or {})
    if specs:
        layout = QFormLayout()
        layout.setSpacing(6)
        for spec in specs:
            spin = QDoubleSpinBox(dialog)
            spin.setRange(spec.minimum, spec.maximum)
            spin.setDecimals(spec.decimals)
            spin.setSingleStep(spec.single_step)
            try:
                spin.setValue(float(stored.get(spec.key, spec.default)))
            except (TypeError, ValueError):
                spin.setValue(spec.default)
            spin.setFixedWidth(120)
            layout.addRow(spec.label, spin)
            form.spins[spec.key] = spin
        outer.addLayout(layout)
    else:
        note = QLabel("No adjustable parameters for this indicator.", dialog)
        note.setStyleSheet("color: palette(placeholder-text);")
        note.setWordWrap(True)
        outer.addWidget(note)

    buttons = QDialogButtonBox(
        QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, dialog
    )
    buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(bool(form.spins))
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    outer.addWidget(buttons)

    dialog._vayren_settings_form = form  # type: ignore[attr-defined]
    return dialog


def read_indicator_settings(dialog: QDialog) -> dict[str, float]:
    """Validated values from a dialog built by :func:`build_indicator_settings_dialog`."""
    form = getattr(dialog, "_vayren_settings_form", None)
    if form is None:
        return {}
    return form.read()
