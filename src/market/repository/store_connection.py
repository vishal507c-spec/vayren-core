"""Read-only candle-store connection policy — the ONE place that opens a store.

Why this module exists
----------------------
The stores are per-symbol SQLite files written by the historical downloader and
they are persisted in WAL mode (header bytes 18/19 == 2). A plain
``sqlite3.connect("file:...?mode=ro")`` against a WAL database still has to build
the WAL index, so SQLite materialises two sidecar files next to the store —
``<SYMBOL>.db-wal`` and ``<SYMBOL>.db-shm`` — on EVERY read. A read-only
connection can neither checkpoint nor unlink them, so they survive the process
and the folder fills with hundreds of permanently-present 0 KB ``-wal`` files.
Nothing is wrong with them; they are litter caused by the OPEN MODE, not by the
data.

The fix is to stop asking SQLite for a lockable connection to a file we only
ever read. ``immutable=1`` declares the file unchangeable for the lifetime of
the connection: no locking, no WAL index, therefore NO sidecars at all. It is
also the fastest read path SQLite offers.

Why that is safe HERE (and why it is still guarded)
----------------------------------------------------
``immutable=1`` trades safety for speed: SQLite does not re-check the file, and
it deliberately IGNORES ``-wal`` contents. So it is only sound when the file
cannot change and has no unmerged frames. Both are established here per open:

1. **Capability probe** — ``immutable`` needs SQLite >= 3.22 and a build without
   ``SQLITE_OMIT_IMMUTABLE``. Probed once against a scratch file, then cached;
   an unsupported runtime silently keeps the locking path.
2. **Frame gate** — immutable is used only when ``<store>-wal`` is absent or
   zero bytes. Zero bytes means zero committed frames, so ignoring the WAL
   cannot hide a row. A non-empty WAL means a writer is mid-flight, and the
   locking path is used instead.
3. **Stability gate** — the store and its WAL are fingerprinted (size +
   ``st_mtime_ns``) before and after the caller's work. Any change means
   something wrote during the read, so the immutable result is DISCARDED and the
   work is re-run through the locking path.

Every failure of the fast path degrades to the original ``mode=ro`` behaviour,
which is exactly what ran before this module existed. The store file is never
opened for writing, never checkpointed, never migrated and never converted.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

logger = logging.getLogger(__name__)

_T = TypeVar("_T")

#: ``immutable=1`` was added in SQLite 3.22.0.
_IMMUTABLE_MIN_VERSION = (3, 22, 0)

_TIMEOUT_SECONDS = 10.0

_ABSENT = -1

_immutable_supported: bool | None = None


def _version_tuple() -> tuple[int, ...]:
    parts: list[int] = []
    for chunk in sqlite3.sqlite_version.split("."):
        digits = "".join(ch for ch in chunk if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def _probe_immutable() -> bool:
    """Can THIS runtime actually open a database with ``immutable=1``?

    A version check alone is not enough: SQLite may be compiled with
    ``SQLITE_OMIT_IMMUTABLE``, in which case the flag is silently ignored
    (the sidecars come back) or rejected. So the flag is proven against a
    scratch file once and the answer is cached for the process.
    """
    global _immutable_supported
    if _immutable_supported is not None:
        return _immutable_supported
    supported = _version_tuple() >= _IMMUTABLE_MIN_VERSION
    if supported:
        handle, name = tempfile.mkstemp(prefix="vayren_immutable_probe_", suffix=".db")
        os.close(handle)
        scratch = Path(name).absolute()
        try:
            seed = sqlite3.connect(scratch)
            try:
                seed.execute("CREATE TABLE probe(x)")
                seed.commit()
            finally:
                seed.close()
            for suffix in ("-wal", "-shm"):
                Path(f"{scratch}{suffix}").unlink(missing_ok=True)
            con = sqlite3.connect(f"{scratch.as_uri()}?mode=ro&immutable=1", uri=True)
            try:
                supported = con.execute("SELECT count(*) FROM probe").fetchone()[0] == 0
            finally:
                con.close()
            if not supported:
                logger.warning(
                    "SQLite %s does not honour immutable=1; stores keep locking reads",
                    sqlite3.sqlite_version,
                )
        except (sqlite3.Error, OSError) as exc:
            logger.warning("immutable=1 probe failed (%s); stores keep locking reads", exc)
            supported = False
        finally:
            for suffix in ("", "-wal", "-shm"):
                Path(f"{scratch}{suffix}").unlink(missing_ok=True)
    _immutable_supported = supported
    return supported


def _uri(path: Path) -> str:
    """Percent-encoded ``file:`` URI; SQLite needs absolute, escaped paths."""
    return path.absolute().as_uri()


def _fingerprint(path: Path) -> tuple[int, int, int, int]:
    """``(store size, store mtime_ns, wal size, wal mtime_ns)``; ``-1`` = absent."""
    try:
        store = path.stat()
    except OSError:
        return (_ABSENT, _ABSENT, _ABSENT, _ABSENT)
    try:
        sidecar = Path(f"{path}-wal").stat()
    except OSError:
        return (store.st_size, store.st_mtime_ns, _ABSENT, _ABSENT)
    return (store.st_size, store.st_mtime_ns, sidecar.st_size, sidecar.st_mtime_ns)


def _wal_is_empty(path: Path) -> bool:
    """No committed frames wait in the WAL, so ignoring it cannot lose a row."""
    try:
        return Path(f"{path}-wal").stat().st_size == 0
    except OSError:
        return True


def _connect(path: Path, immutable: bool) -> sqlite3.Connection:
    flags = "mode=ro&immutable=1" if immutable else "mode=ro"
    return sqlite3.connect(f"{_uri(path)}?{flags}", uri=True, timeout=_TIMEOUT_SECONDS)


def read_store(path: Path, run: Callable[[sqlite3.Connection], _T]) -> _T:
    """Run ``run(connection)`` against ``path`` read-only and return its result.

    The sidecar-free ``immutable=1`` path is tried first whenever the store holds
    no unmerged WAL frames, then re-run through the locking ``mode=ro`` path if
    the store turned out to be changing underneath us. The connection is always
    closed before returning and the store is never opened for writing, so the
    caller sees the same rows it always saw — only the open mode differs.
    """
    store = Path(path)
    if _probe_immutable() and _wal_is_empty(store):
        before = _fingerprint(store)
        try:
            con = _connect(store, immutable=True)
            try:
                result = run(con)
                stable = _fingerprint(store) == before
            finally:
                con.close()
        except (sqlite3.Error, OSError) as exc:
            logger.warning(
                "%s: immutable fast-path read failed (%s); retrying through the locking path",
                store.name,
                exc,
            )
        else:
            if stable:
                return result
            logger.info(
                "%s changed during an immutable read; retrying through the locking path",
                store.name,
            )
    con = _connect(store, immutable=False)
    try:
        return run(con)
    finally:
        con.close()


__all__ = ["read_store"]
