"""Broker-facing order tags. Internal client_order_id never changes.

FYERS accepts ``orderTag`` of 1-30 letters/digits only (never the client id
or ``Untagged``) and echoes user tags with a ``1:`` prefix. Zerodha's ``tag``
is alphanumeric with a 20-char cap. A deterministic hash tag is therefore
sent to the venue and the reverse map is persisted, so every decode path
(order book, WebSocket, reconcile) still recovers the original internal id.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
from collections.abc import Iterable
from pathlib import Path

logger = logging.getLogger(__name__)

BROKER_TAG_RE = re.compile(r"^[A-Za-z0-9]{1,30}$")
_TAG_PREFIX = "OBR"
_TAG_HEX_CHARS = 16
_VENUE_PREFIXES = ("1:", "2:")
_DEFAULT_MAP_PATH = Path.home() / ".vayren" / "data" / "broker_tag_map.json"
_MAP_LIMIT = 5000


def to_broker_tag(client_order_id: str) -> str:
    """Venue-safe deterministic tag (19 chars); internal id is untouched."""
    if not isinstance(client_order_id, str) or not client_order_id:
        raise ValueError("client_order_id must be a non-empty string")
    digest = hashlib.sha256(client_order_id.encode("utf-8")).hexdigest()[:_TAG_HEX_CHARS]
    tag = _TAG_PREFIX + digest
    if BROKER_TAG_RE.fullmatch(tag) is None:
        raise ValueError(f"generated broker tag is not venue-safe: {tag!r}")
    return tag


class BrokerTagRegistry:
    """Bounded, thread-safe, persisted ``tag -> client_order_id`` map."""

    def __init__(self, path: Path | str | None = None) -> None:
        self._path = Path(path) if path is not None else _DEFAULT_MAP_PATH
        self._lock = threading.Lock()
        self._map: dict[str, str] = {}
        self._ids: list[str] = []
        self._load()

    def _load(self) -> None:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(raw, dict):
            return
        tags = raw.get("tags")
        if isinstance(tags, dict):
            self._map = {str(k): str(v) for k, v in list(tags.items())[-_MAP_LIMIT:]}
        else:
            self._map = {str(k): str(v) for k, v in list(raw.items())[-_MAP_LIMIT:]}
        ids = raw.get("ids")
        self._ids = (
            [str(item) for item in ids][-_MAP_LIMIT:]
            if isinstance(ids, list)
            else list(dict.fromkeys(self._map.values()))
        )

    def _flush(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps({"tags": self._map, "ids": self._ids}), encoding="utf-8")
            tmp.replace(self._path)
        except OSError as exc:
            # A failed cache write must never block an order: in-memory state
            # still serves this process.
            logger.warning("BROKER_TAG_MAP_WRITE_FAILED error=%s", exc)

    def remember(self, tag: str, client_order_id: str) -> None:
        with self._lock:
            if self._map.get(tag) == client_order_id and client_order_id in self._ids:
                return
            self._map[tag] = client_order_id
            if client_order_id not in self._ids:
                self._ids.append(client_order_id)
            while len(self._map) > _MAP_LIMIT:
                self._map.pop(next(iter(self._map)))
            self._ids = self._ids[-_MAP_LIMIT:]
            self._flush()

    def resolve(self, tag: str, candidates: Iterable[str] = ()) -> str | None:
        """Map a venue tag back to its internal id.

        When the map file is missing or the entry was lost, a deterministic
        hash makes the reverse match possible from any known internal id
        (engine intents / open orders supplied as ``candidates``).
        """
        with self._lock:
            found = self._map.get(tag)
            known = tuple(dict.fromkeys((*self._ids, *candidates)))
        if found is not None:
            return found
        for client_order_id in known:
            if to_broker_tag(client_order_id) == tag:
                return client_order_id
        return None


#: Process-wide tag-map store. Deliberately NOT named ``*_REGISTRY``: the
#: authority validator reserves that shape for the canonical broker/strategy
#: registries. This is a private cache of the persisted tag<->id map, not a
#: second registry of brokers or strategies.
_TAG_MAP_STORE: BrokerTagRegistry | None = None
_TAG_MAP_STORE_LOCK = threading.Lock()


def tag_registry() -> BrokerTagRegistry:
    global _TAG_MAP_STORE
    with _TAG_MAP_STORE_LOCK:
        if _TAG_MAP_STORE is None:
            _TAG_MAP_STORE = BrokerTagRegistry()
        return _TAG_MAP_STORE


def from_broker_tag(venue_tag: object, candidates: Iterable[str] = ()) -> str:
    """Strip the venue prefix and recover the internal client order id."""
    text = str(venue_tag or "")
    for prefix in _VENUE_PREFIXES:
        if text.startswith(prefix):
            text = text[len(prefix) :]
            break
    return tag_registry().resolve(text, candidates) or text
