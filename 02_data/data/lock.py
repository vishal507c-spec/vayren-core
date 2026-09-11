"""Single-instance engine lock — PID file with heartbeat and stale detection.

Preserved verbatim from the original engine (``EngineLock``). The lock file
lives inside the data directory and is disposable: deleting it never loses
progress because candle databases are the only source of truth.
"""

from __future__ import annotations

import logging
import os
import platform
from datetime import datetime
from pathlib import Path

log = logging.getLogger("HistDownloadEngine")


class EngineLock:
    def __init__(self, path: str | Path, stale_seconds: int = 120) -> None:
        self._path = Path(path)
        self._stale_seconds = stale_seconds
        self._pid = os.getpid()

    def try_acquire(self) -> bool:
        existing = self._read()
        if existing:
            other_pid_raw = existing.get("pid", 0)
            other_host = existing.get("hostname", "")
            hb_str = existing.get("heartbeat", "")

            try:
                other_pid = int(other_pid_raw)
            except (TypeError, ValueError):
                other_pid = 0

            if other_pid == self._pid and other_host == platform.node():
                self._write()
                return True

            try:
                hb_dt = datetime.fromisoformat(hb_str)
                age_sec = (datetime.now() - hb_dt).total_seconds()
            except Exception:
                age_sec = self._stale_seconds + 1

            pid_alive = False
            if other_host == platform.node():
                pid_alive = _pid_alive(other_pid)

            if not pid_alive or age_sec > self._stale_seconds:
                log.warning(f"[Lock] Stale lock (pid={other_pid}, age={age_sec:.0f}s). Clearing.")
                self._clear()
            else:
                log.error(f"[Lock] Engine already running (pid={other_pid}). Exiting.")
                return False

        self._write()
        log.info(f"[Lock] Acquired (pid={self._pid})")
        return True

    def release(self) -> None:
        self._clear()
        log.info("[Lock] Released.")

    def heartbeat(self) -> None:
        try:
            existing = self._read()
            if existing is None:
                return
            existing["heartbeat"] = datetime.now().isoformat()
            with open(self._path, "w") as f:
                for k, v in existing.items():
                    f.write(f"{k}={v}\n")
        except Exception:
            pass

    def _write(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        now = datetime.now().isoformat()
        with open(self._path, "w") as f:
            f.write(f"pid={self._pid}\n")
            f.write(f"hostname={platform.node()}\n")
            f.write(f"started={now}\n")
            f.write(f"heartbeat={now}\n")

    def _read(self) -> dict[str, str] | None:
        if not self._path.is_file():
            return None
        try:
            data: dict[str, str] = {}
            with open(self._path) as f:
                for line in f:
                    line = line.strip()
                    if "=" in line:
                        k, v = line.split("=", 1)
                        data[k.strip()] = v.strip()
            return data if data else None
        except Exception:
            return None

    def _clear(self) -> None:
        try:
            self._path.unlink()
        except FileNotFoundError:
            pass
        except Exception:
            pass


def _pid_alive(pid: int) -> bool:
    try:
        if platform.system() == "Windows":
            import ctypes

            h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
            if h:
                ctypes.windll.kernel32.CloseHandle(h)
                return True
            return False
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False
