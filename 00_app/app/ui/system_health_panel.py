"""SystemHealthPanel — real CPU/RAM/disk + engine state display."""

from __future__ import annotations

import ctypes
import shutil
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QGridLayout, QLabel, QVBoxLayout, QWidget

_LABEL_STYLE = "color: palette(placeholder-text); font-size: 10px; font-weight: 700;"
_KEY_STYLE = "color: palette(placeholder-text); font-size: 11px;"
_VALUE_OK = "color: #26a69a; font-size: 11px; font-weight: 600;"
_VALUE_BAD = "color: #ef5350; font-size: 11px; font-weight: 600;"
_VALUE_NEUTRAL = "color: palette(text); font-size: 11px; font-weight: 600;"
_VALUE_MUTED = "color: palette(placeholder-text); font-size: 11px;"


def _cpu_times() -> tuple[int, int, int] | None:
    """Return (idle, kernel, user) 100ns ticks via GetSystemTimes, or None."""
    try:
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        idle = ctypes.c_ulonglong()
        kernel = ctypes.c_ulonglong()
        user = ctypes.c_ulonglong()
        ok = kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user))
        if not ok:
            return None
        return (int(idle.value), int(kernel.value), int(user.value))
    except Exception:  # noqa: BLE001
        return None


def _ram_percent() -> str:
    """Return RAM usage percent string or ``"--"`` on failure."""
    try:

        class _MemStatus(ctypes.Structure):  # type: ignore[misc]
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        stat = _MemStatus()
        stat.dwLength = ctypes.sizeof(_MemStatus)
        ok = ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))  # type: ignore[attr-defined]
        if not ok:
            return "--"
        return f"{int(stat.dwMemoryLoad)}%"
    except Exception:  # noqa: BLE001
        return "--"


class SystemHealthPanel(QWidget):
    """Real system health: polled CPU/RAM/disk + event-driven engine states."""

    def __init__(self, data_dir: str | Path | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._data_dir = Path(data_dir) if data_dir is not None else None
        self._engine_states: dict[str, str] = {}
        self._last_cpu: tuple[int, int, int] | None = None
        self._last_cpu_pct: str = "--"

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(4)

        caption = QLabel("SYSTEM HEALTH", self)
        caption.setStyleSheet(_LABEL_STYLE)
        layout.addWidget(caption)

        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(2)

        self._rows: dict[str, QLabel] = {}
        for row, key in enumerate(
            (
                "CPU",
                "RAM",
                "Disk",
                "Data Engine",
                "Chart Engine",
                "Strategy Engine",
                "Backtest Engine",
            )
        ):
            key_label = QLabel(key, self)
            key_label.setStyleSheet(_KEY_STYLE)
            value_label = QLabel("--", self)
            value_label.setStyleSheet(_VALUE_MUTED)
            value_label.setTextFormat(
                __import__("PySide6.QtCore", fromlist=["Qt"]).Qt.TextFormat.PlainText
            )
            grid.addWidget(key_label, row, 0)
            grid.addWidget(value_label, row, 1)
            self._rows[key] = value_label

        layout.addLayout(grid)
        layout.addStretch(1)

        self._timer = QTimer(self)
        self._timer.setInterval(3000)
        self._timer.timeout.connect(self._poll)
        self._timer.start()
        self._poll()

    def set_engine_state(self, engine: str, state: str) -> None:
        """Update one engine row to ``state`` (e.g. ``"Ready"``/``"Running"``)."""
        self._engine_states[engine] = state
        label = self._rows.get(engine)
        if label is None:
            return
        label.setText(state)
        if state in ("Running", "Busy", "Active"):
            label.setStyleSheet(_VALUE_OK)
        elif state in ("Failed", "Unavailable", "Error"):
            label.setStyleSheet(_VALUE_BAD)
        elif state == "--":
            label.setStyleSheet(_VALUE_MUTED)
        else:
            label.setStyleSheet(_VALUE_NEUTRAL)

    def _poll(self) -> None:
        # RAM
        self._rows["RAM"].setText(_ram_percent())
        # Disk
        try:
            path = str(self._data_dir) if self._data_dir is not None else "C:\\"
            usage = shutil.disk_usage(path)
            pct = int(usage.used / usage.total * 100) if usage.total else 0
            self._rows["Disk"].setText(f"{pct}%")
        except Exception:  # noqa: BLE001
            self._rows["Disk"].setText("--")
        # CPU (delta of GetSystemTimes)
        times = _cpu_times()
        if times is not None and self._last_cpu is not None:
            idle_d = times[0] - self._last_cpu[0]
            kernel_d = times[1] - self._last_cpu[1]
            user_d = times[2] - self._last_cpu[2]
            total = kernel_d + user_d
            if total > 0:
                cpu_pct = int((total - idle_d) * 100 / total)
                cpu_pct = max(0, min(100, cpu_pct))
                self._last_cpu_pct = f"{cpu_pct}%"
        if times is not None:
            self._last_cpu = times
        self._rows["CPU"].setText(self._last_cpu_pct)

    def stop(self) -> None:
        """Stop the polling timer (call before destroying)."""
        self._timer.stop()
