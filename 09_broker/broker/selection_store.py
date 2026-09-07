"""FileSelectionStore — JSON persistence for the authoritative BrokerSelection
(design §8: workspace-scoped file under the data dir, no secrets).

Schema (deterministic, versioned)::

    {"kind": "vayren.broker_selection", "version": 1, "name": str,
     "environment": "paper"|"sandbox"|"live", "selected_at": ISO-8601,
     "reason": str}

Fail-closed rules: a missing file is simply no selection; a malformed,
unknown-kind, unknown-version or unknown-broker-environment file raises
:class:`SelectionLoadError` (callers surface the reason — never silently
fall back to an arbitrary broker). Writes are atomic (temp file + replace)
and carry no secret values — the schema has no field for them.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from broker.selection import BrokerSelection, SelectionError
from broker.vocab import Environment

_SCHEMA_KIND = "vayren.broker_selection"
_SCHEMA_VERSION = 1


class SelectionLoadError(RuntimeError):
    """The persisted selection is unreadable or invalid — fail-closed."""


class FileSelectionStore:
    """File-backed :class:`SelectionStore` (atomic replace, schema-checked)."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    @property
    def path(self) -> Path:
        """Location of the backing JSON file."""
        return self._path

    def load(self) -> BrokerSelection | None:
        """Load the selection; missing file → None, corrupt file → error."""
        if not self._path.is_file():
            return None
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SelectionLoadError(f"selection file unreadable: {exc}") from exc
        if not isinstance(raw, dict):
            raise SelectionLoadError("selection file must contain a JSON object")
        if raw.get("kind") != _SCHEMA_KIND or raw.get("version") != _SCHEMA_VERSION:
            raise SelectionLoadError(
                f"unknown selection schema: kind={raw.get('kind')!r} version={raw.get('version')!r}"
            )
        try:
            return BrokerSelection(
                name=str(raw["name"]),
                environment=Environment(raw["environment"]),
                selected_at=str(raw["selected_at"]),
                reason=str(raw["reason"]),
            )
        except KeyError as exc:
            raise SelectionLoadError(f"selection file missing field: {exc}") from exc
        except SelectionError as exc:
            raise SelectionLoadError(f"selection file invalid: {exc}") from exc
        except ValueError as exc:
            raise SelectionLoadError(f"selection file invalid: {exc}") from exc

    def save(self, selection: BrokerSelection) -> None:
        """Persist atomically; parent dirs are created on demand."""
        payload = {
            "kind": _SCHEMA_KIND,
            "version": _SCHEMA_VERSION,
            "name": selection.name,
            "environment": selection.environment.value,
            "selected_at": selection.selected_at,
            "reason": selection.reason,
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        handle = tempfile.NamedTemporaryFile(  # noqa: SIM115
            "w", encoding="utf-8", dir=self._path.parent, delete=False, suffix=".tmp"
        )
        try:
            json.dump(payload, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
            handle.close()
            os.replace(handle.name, self._path)
        except OSError:
            Path(handle.name).unlink(missing_ok=True)
            raise

    def clear(self) -> None:
        """Remove the selection file (absent file is already cleared)."""
        self._path.unlink(missing_ok=True)
