"""ChartSession persistence — reliable local JSON store for workspace restoration.

Stores last symbol, timeframe, active indicators (name, visibility, settings).
Uses atomic write to survive crash/force-close. Falls back to defaults if
corrupted. No chart rendering or OBR calculation logic here — pure state.
"""

from __future__ import annotations

import json
import os
import platform
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class IndicatorState:
    name: str
    visible: bool = True
    settings: dict[str, Any] = field(default_factory=dict)


@dataclass
class ChartSession:
    symbol: str | None = None
    timeframe: str | None = None
    indicators: list[IndicatorState] = field(default_factory=list)
    # chart layout state — viewport follow flag and price range is optional
    # kept for future layout persistence; not required for basic restore
    chart_layout: dict[str, Any] = field(default_factory=dict)
    version: int = 1


def _app_data_dir() -> Path | None:
    """Platform-standard writable app-data folder (Qt AppDataLocation parity).

    Windows: ``%APPDATA%`` (Roaming). macOS: ``~/Library/Application Support``.
    Posix: ``$XDG_CONFIG_HOME`` else ``~/.config``. Returns ``None`` when no
    standard folder is resolvable so the caller degrades to a user folder.
    """
    system = platform.system()
    if system == "Windows":
        appdata = os.environ.get("APPDATA")
        return Path(appdata) if appdata else None
    if system == "Darwin":
        try:
            return Path.home() / "Library" / "Application Support"
        except Exception:
            return None
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg)
    try:
        return Path.home() / ".config"
    except Exception:
        return None


def _default_session_path() -> Path:
    # Prefer the platform app-data folder (platform standard, survives crash,
    # reliable). Use a VAYREN subfolder so the store is app-specific.
    base = _app_data_dir()
    if base is not None:
        base_path = Path(base)
        # base may already be app-specific, or generic (e.g. Roaming)
        # Ensure the VAYREN subfolder for reliability
        if base_path.name.lower() not in ("vayren", "vayren-core"):
            p = base_path / "VAYREN" / "session.json"
        else:
            p = base_path / "session.json"
        return p
    try:
        return Path.home() / ".vayren" / "session.json"
    except Exception:
        return Path.cwd() / ".vayren_session.json"


class ChartSessionStore:
    """Reliable JSON store for ChartSession.

    - Atomic write via temp+rename
    - Corrupted/incomplete file → defaults
    - No-ops on write failure (logs)
    """

    def __init__(self, path: Path | str | None = None) -> None:
        self._path = Path(path) if path is not None else _default_session_path()
        self._session: ChartSession | None = None

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> ChartSession:
        try:
            if not self._path.exists():
                return ChartSession()
            text = self._path.read_text(encoding="utf-8")
            data = json.loads(text)
            if not isinstance(data, dict):
                return ChartSession()
            symbol = data.get("symbol")
            timeframe = data.get("timeframe")
            symbol = None if not isinstance(symbol, str) or not symbol.strip() else symbol.strip()
            timeframe = (
                None
                if not isinstance(timeframe, str) or not timeframe.strip()
                else timeframe.strip()
            )
            raw_inds = data.get("indicators") or []
            indicators: list[IndicatorState] = []
            if isinstance(raw_inds, list):
                for item in raw_inds:
                    if not isinstance(item, dict):
                        continue
                    name = item.get("name")
                    if not isinstance(name, str) or not name.strip():
                        continue
                    visible = item.get("visible", True)
                    if not isinstance(visible, bool):
                        visible = bool(visible)
                    settings = item.get("settings") or {}
                    if not isinstance(settings, dict):
                        settings = {}
                    indicators.append(
                        IndicatorState(
                            name=name.strip(),
                            visible=visible,
                            settings=dict(settings),
                        )
                    )
            chart_layout = data.get("chart_layout") or {}
            if not isinstance(chart_layout, dict):
                chart_layout = {}
            version = data.get("version", 1)
            try:
                version = int(version)
            except Exception:
                version = 1
            sess = ChartSession(
                symbol=symbol,
                timeframe=timeframe,
                indicators=indicators,
                chart_layout=dict(chart_layout),
                version=version,
            )
            self._session = sess
            return sess
        except Exception:
            # corrupted → defaults
            return ChartSession()

    def save(self, session: ChartSession) -> None:
        try:
            data = asdict(session)
            # ensure parent exists
            self._path.parent.mkdir(parents=True, exist_ok=True)
            # atomic write
            fd, tmp = tempfile.mkstemp(dir=str(self._path.parent), prefix=".session_tmp_")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
                    f.flush()
                    os.fsync(f.fileno())
                # atomic rename
                os.replace(tmp, self._path)
            finally:
                # cleanup temp if still exists (replace succeeded → tmp gone)
                try:
                    if os.path.exists(tmp):
                        os.remove(tmp)
                except Exception:
                    pass
            self._session = session
        except Exception:
            # don't crash on save failure
            pass

    def update(self, **kwargs: Any) -> ChartSession:
        """Convenience: load, patch fields, save."""
        sess = self.load()
        for k, v in kwargs.items():
            if hasattr(sess, k):
                setattr(sess, k, v)
        self.save(sess)
        return sess

    def clear(self) -> None:
        try:
            if self._path.exists():
                self._path.unlink()
        except Exception:
            pass
        self._session = ChartSession()
