"""MarketDataRouter — provider-agnostic routing + health + failover (Phase 4).

Owns provider selection, failover state, and freshness policy for
canonical instruments. Strategy code asks ``get_readiness(id)`` and
consumes :class:`NormalizedMarketData`; it never sees feeds, tokens,
symbols, connections, or failover logic.

Design rules (spec §2, §16–§18):
- One ACTIVE provider per instrument — rows from any other provider are
  dropped, never merged (no contradictory state, deterministic).
- Failover is explicit and observable (journaled); stale data never
  flows silently and no prices are ever fabricated.
- Identity continuity: the published ``instrument_id`` never changes on
  failover; only the diagnostic ``provider`` field moves.
- Stickiness + hysteresis: no re-evaluation while the current provider
  is usable; failback only after the primary stays healthy for the full
  recovery window (no flapping).

Feeds are duck-typed (``connect/disconnect/subscribe/unsubscribe/poll/
health`` over plain dict rows — the MarketDataFace shape) and INJECTED:
the router never constructs transports, never touches credentials, and
never imports provider SDKs or broker modules. Real feeds live in
``broker/providers/*/router_feed.py``; tests use fakes.

Deliberately NOT here (future phases): broker execution routing, order
flow, risk/strategy redesign, dynamic universes, explainability.
"""

from __future__ import annotations

import contextlib
import datetime
import logging
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from strategy.instrument_registry import get_instrument_registry
from strategy.models.market_data import (
    DATA_RECEIVED,
    DATA_STALE,
    DISABLED,
    DISCONNECTED,
    ERROR,
    FAILBACK_COMPLETED,
    FAILOVER,
    FAILOVER_COMPLETED,
    FAILOVER_STARTED,
    HEALTHY,
    NO_DATA_AVAILABLE,
    PRIMARY_RECOVERED,
    PROVIDER_CONNECTED,
    PROVIDER_DISCONNECTED,
    READINESS_ERROR,
    READY,
    STALE,
    SUBSCRIPTION_CONFIRMED,
    SUBSCRIPTION_STARTED,
    UNAVAILABLE,
    WAITING_FOR_DATA,
    NormalizedMarketData,
    RouterConfig,
)
from strategy.models.provider_instrument import FOUND, MAPPED
from strategy.provider_mapping import ProviderMappingRegistry

log = logging.getLogger(__name__)


@runtime_checkable
class MarketDataFeed(Protocol):
    """Structural feed contract (the MarketDataFace shape, duck-typed).

    ``poll`` returns plain-dict rows (never SDK objects)::

        {"provider_instrument_id": str, "price": float,
         "event_time": ISO-8601 (optional), "volume": float (optional),
         "bid"/"ask": float (optional), "seq": int (optional)}

    The router resolves every row to canonical identity itself — a feed
    never names a canonical instrument.
    """

    def connect(self) -> None: ...
    def disconnect(self) -> None: ...
    def subscribe(self, symbols: tuple[str, ...]) -> None: ...
    def unsubscribe(self, symbols: tuple[str, ...]) -> None: ...
    def poll(self) -> tuple[dict[str, Any], ...]: ...
    def health(self) -> tuple[bool, str]: ...


@dataclass
class _Subscription:
    """Routing state for one canonical instrument (never shared)."""

    canonical_id: str
    exchange: str
    segment: str
    symbol: str
    # Active source provider (renamed off the governed `.provider` write
    # shape — routing ownership lives here, not in BrokerSelection).
    active_provider: str = ""
    confirmed: bool = False
    last_tick_at: float | None = None
    last_event_iso: str = ""
    last_price: float | None = None
    last_volume: float | None = None
    last_bid: float | None = None
    last_ask: float | None = None
    router_seq: int = 0
    failed_over: bool = False
    primary_healthy_since: float | None = None
    pending_completion: str = ""
    stale_reported: bool = False
    last_error: str = ""


class RouterError(RuntimeError):
    """Refused router operation (unknown instrument, bad wiring)."""


def _utcnow_iso() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat()


class MarketDataRouter:
    """Routes canonical instruments across provider feeds (Phase 4)."""

    def __init__(
        self,
        mappings: ProviderMappingRegistry,
        config: RouterConfig | None = None,
        feeds: dict[str, Any] | None = None,
        clock: Callable[[], float] | None = None,
        event_cap: int = 500,
    ) -> None:
        self._mappings = mappings
        self._config = config or RouterConfig()
        self._feeds: dict[str, Any] = dict(feeds or {})
        self._clock = clock or time.monotonic
        self._lock = threading.RLock()
        self._subs: dict[str, _Subscription] = {}
        self._last_seq: dict[tuple[str, str], int] = {}
        self._feed_error: dict[str, str] = {}
        self._dropped_unmapped: dict[str, int] = {}
        self._events: deque[dict[str, Any]] = deque(maxlen=max(1, event_cap))

    # ── wiring ──────────────────────────────────────────────────────

    def add_feed(self, provider: str, feed: Any) -> None:
        """Attach one feed (structural contract checked, never trusted)."""
        name = str(provider or "").strip().upper()
        if not name:
            raise RouterError("provider name must be non-empty")
        if not isinstance(feed, MarketDataFeed):
            raise RouterError(f"feed for {name!r} violates the MarketDataFeed contract")
        with self._lock:
            self._feeds[name] = feed
            self._record(PROVIDER_CONNECTED, "", name, "feed attached")

    # ── subscription lifecycle (spec §14) ───────────────────────────

    def subscribe(self, canonical_id: str) -> str:
        """Request data for one canonical instrument → active provider name.

        Resolves the instrument, picks the first usable provider in policy
        order, and subscribes its provider id. Requested ≠ ready: readiness
        stays WAITING_FOR_DATA until the first fresh tick confirms.
        Raises RouterError for unknown instruments; returns "" with an
        UNAVAILABLE journal entry when nothing is usable (never raises for
        provider outages — canonical identity survives them).
        """
        instrument = get_instrument_registry().get(canonical_id)
        if instrument is None:
            raise RouterError(f"unknown canonical instrument: {canonical_id!r}")
        with self._lock:
            sub = self._subs.get(canonical_id)
            if sub is None:
                sub = _Subscription(
                    canonical_id=canonical_id,
                    exchange=instrument.exchange,
                    segment=instrument.segment,
                    symbol=instrument.symbol,
                )
                self._subs[canonical_id] = sub
            provider = self._first_usable_provider(canonical_id)
            if not provider:
                self._record(
                    NO_DATA_AVAILABLE, canonical_id, "", self._unavailable_reason(canonical_id)
                )
                return ""
            self._activate(sub, provider, fresh_request=True)
            return provider

    def unsubscribe(self, canonical_id: str) -> None:
        """Drop one subscription (best-effort provider unsubscribe)."""
        with self._lock:
            sub = self._subs.pop(canonical_id, None)
        if sub is not None and sub.active_provider:
            self._unsubscribe_quietly(sub.active_provider, canonical_id)

    def close(self) -> None:
        """Tear down every subscription and disconnect every feed."""
        with self._lock:
            subs = list(self._subs.values())
            self._subs.clear()
        for sub in subs:
            if sub.active_provider:
                self._unsubscribe_quietly(sub.active_provider, sub.canonical_id)
        with self._lock:
            feeds = list(self._feeds.items())
        for _name, feed in feeds:
            with contextlib.suppress(Exception):
                feed.disconnect()

    # ── streaming ───────────────────────────────────────────────────

    def poll(self) -> tuple[NormalizedMarketData, ...]:
        """Drain every feed once → normalized ticks for active providers.

        Only rows from each instrument's ACTIVE provider publish; anything
        else (unknown ids, unmapped rows, duplicates, older-or-equal event
        times, non-positive prices) is dropped with a bounded journal note
        on first sight — never merged, never fabricated.
        """
        now = self._clock()
        received_iso = _utcnow_iso()
        published: list[NormalizedMarketData] = []
        with self._lock:
            feeds = list(self._feeds.items())
        for provider, feed in feeds:
            try:
                rows = feed.poll()
            except Exception as exc:  # noqa: BLE001
                self._note_feed_error(provider, f"poll failed: {exc}")
                continue
            for row in rows or ():
                tick = self._normalize_row(provider, row, now, received_iso)
                if tick is not None:
                    published.append(tick)
        self.evaluate()
        return tuple(published)

    def evaluate(self) -> None:
        """Freshness sweep: fail over stale instruments, recover primaries.

        Stateful and sticky — a usable current provider is never
        re-evaluated away; switches happen only from unusable to usable.
        Safe to call on every poll and idle-safe with no ticks at all.
        """
        now = self._clock()
        with self._lock:
            subs = list(self._subs.values())
        for sub in subs:
            self._evaluate_subscription(sub, now)

    # ── strategy transparency (spec §21) ────────────────────────────

    def get_readiness(self, canonical_id: str) -> tuple[str, str]:
        """(readiness, reason) for one subscribed instrument.

        READY / WAITING_FOR_DATA / STALE / FAILOVER / UNAVAILABLE / ERROR.
        Raises RouterError for never-subscribed ids (explicit, not silent).
        """
        with self._lock:
            sub = self._subs.get(canonical_id)
            if sub is None:
                raise RouterError(f"not subscribed: {canonical_id!r}")
            return self._readiness(sub, self._clock())

    def get_active_provider(self, canonical_id: str) -> str:
        """Active provider name ("" when none is usable)."""
        with self._lock:
            sub = self._subs.get(canonical_id)
            if sub is None:
                raise RouterError(f"not subscribed: {canonical_id!r}")
            return sub.active_provider

    # ── diagnostics (spec §22–§23, API-level) ───────────────────────

    def provider_health(self, provider: str | None = None) -> dict[str, dict[str, str]]:
        """Provider health snapshot (HEALTHY/DEGRADED/STALE/DISCONNECTED/ERROR/DISABLED)."""
        with self._lock:
            names = (
                [provider.strip().upper()]
                if provider is not None
                else list(self._config.ordered_providers)
            )
            return {name: self._provider_health(name) for name in names}

    def instrument_states(self) -> dict[str, dict[str, Any]]:
        """Per-instrument routing snapshot (provider + readiness + reason)."""
        now = self._clock()
        with self._lock:
            out: dict[str, dict[str, Any]] = {}
            for canonical_id, sub in sorted(self._subs.items()):
                readiness, reason = self._readiness(sub, now)
                out[canonical_id] = {
                    "provider": sub.active_provider,
                    "readiness": readiness,
                    "reason": reason,
                    "failed_over": sub.failed_over,
                }
            return out

    def events(self) -> tuple[dict[str, Any], ...]:
        """Bounded structured routing journal (debugging, not telemetry)."""
        with self._lock:
            return tuple(self._events)

    def diagnostics(self) -> dict[str, Any]:
        """Combined routing visibility (providers + instruments + config)."""
        with self._lock:
            dropped = dict(self._dropped_unmapped)
        return {
            "primary": self._config.primary,
            "secondaries": list(self._config.secondaries),
            "providers": self.provider_health(),
            "instruments": self.instrument_states(),
            "dropped_unmapped": dropped,
        }

    # ── internals ───────────────────────────────────────────────────

    def _record(self, kind: str, canonical_id: str, provider: str, reason: str) -> None:
        self._events.append(
            {
                "type": kind,
                "instrument": canonical_id,
                "provider": provider,
                "reason": reason,
                "timestamp": _utcnow_iso(),
            }
        )

    def _note_feed_error(self, provider: str, reason: str) -> None:
        with self._lock:
            self._feed_error[provider] = reason
            self._record(PROVIDER_DISCONNECTED, "", provider, reason)
        log.warning("market-data feed %s error: %s", provider, reason)

    def _feed_usable(self, provider: str) -> tuple[bool, str]:
        """Transport usable right now (health() true, no fresh error)."""
        feed = self._feeds.get(provider)
        if feed is None:
            return False, f"no {provider} feed attached"
        if self._feed_error.get(provider):
            return False, self._feed_error[provider]
        try:
            healthy, reason = feed.health()
        except Exception as exc:  # noqa: BLE001
            return False, f"health check failed: {exc}"
        if not healthy:
            return False, str(reason or "reported unhealthy")
        return True, str(reason or "healthy")

    def _provider_health(self, provider: str) -> dict[str, str]:
        if provider not in self._feeds:
            return {"state": DISABLED, "reason": "no feed attached"}
        if self._feed_error.get(provider):
            return {"state": ERROR, "reason": self._feed_error[provider]}
        try:
            healthy, reason = self._feeds[provider].health()
        except Exception as exc:  # noqa: BLE001
            return {"state": ERROR, "reason": f"health check failed: {exc}"}
        if healthy:
            return {"state": HEALTHY, "reason": str(reason or "healthy")}
        reason_text = str(reason or "reported unhealthy").lower()
        if "connect" in reason_text or "subscribed" in reason_text:
            return {"state": DISCONNECTED, "reason": str(reason or "disconnected")}
        return {"state": ERROR, "reason": str(reason or "error")}

    def _mapping_usable(self, canonical_id: str, provider: str) -> tuple[bool, str]:
        lookup = self._mappings.get_mapping(canonical_id, provider)
        if lookup.status == FOUND and lookup.provider_instrument is not None:
            return True, ""
        if lookup.status == DISABLED:
            return False, "mapping disabled"
        if lookup.status == "CONFLICT":
            return False, "mapping conflict"
        return False, "UNMAPPED"

    def _first_usable_provider(self, canonical_id: str) -> str:
        for provider in self._config.ordered_providers:
            usable, _ = self._mapping_usable(canonical_id, provider)
            if not usable:
                continue
            feed_usable, _ = self._feed_usable(provider)
            if feed_usable:
                return provider
        return ""

    def _unavailable_reason(self, canonical_id: str) -> str:
        for provider in self._config.ordered_providers:
            mapping_usable, mapping_reason = self._mapping_usable(canonical_id, provider)
            if not mapping_usable:
                if mapping_reason == "UNMAPPED":
                    continue
                return mapping_reason
            _feed_usable, feed_reason = self._feed_usable(provider)
            if _feed_usable:
                return "no usable provider state"
            if "no " in feed_reason and "feed attached" in feed_reason:
                continue
            return "NO_HEALTHY_PROVIDER"
        return "UNMAPPED"

    def _provider_ids(self, canonical_id: str, provider: str) -> tuple[str, ...]:
        lookup = self._mappings.get_mapping(canonical_id, provider)
        if lookup.status == FOUND and lookup.provider_instrument is not None:
            return (lookup.provider_instrument.provider_instrument_id,)
        return ()

    def _activate(self, sub: _Subscription, provider: str, fresh_request: bool) -> None:
        ids = self._provider_ids(sub.canonical_id, provider)
        feed = self._feeds.get(provider)
        if feed is None or not ids:
            return
        try:
            feed.subscribe(tuple(ids))
        except Exception as exc:  # noqa: BLE001
            sub.last_error = f"subscribe failed: {exc}"
            self._note_feed_error(provider, sub.last_error)
            return
        sub.active_provider = provider
        if fresh_request:
            sub.confirmed = False
            sub.last_tick_at = None
            sub.pending_completion = ""
            self._record(SUBSCRIPTION_STARTED, sub.canonical_id, provider, ",".join(ids))

    def _switch_provider(self, sub: _Subscription, provider: str, started_event: str) -> None:
        previous = sub.active_provider
        if previous:
            self._unsubscribe_quietly(previous, sub.canonical_id)
        self._record(started_event, sub.canonical_id, provider, f"from {previous or 'none'}")
        self._activate(sub, provider, fresh_request=False)
        sub.confirmed = False
        # A new source restarts ordering: its first tick is the freshest
        # knowledge from THAT stream, even when its stamp predates the old
        # source's last tick (clocks/granularity differ across providers).
        sub.last_event_iso = ""
        sub.last_price = None
        sub.last_volume = None
        sub.last_bid = None
        sub.last_ask = None
        sub.pending_completion = {
            FAILOVER_STARTED: FAILOVER_COMPLETED,
            PRIMARY_RECOVERED: FAILBACK_COMPLETED,
            FAILBACK_COMPLETED: FAILBACK_COMPLETED,
        }.get(started_event, "")

    def _unsubscribe_quietly(self, provider: str, canonical_id: str) -> None:
        feed = self._feeds.get(provider)
        if feed is None:
            return
        with contextlib.suppress(Exception):
            feed.unsubscribe(self._provider_ids(canonical_id, provider))

    def _normalize_row(
        self, provider: str, row: Any, now: float, received_iso: str
    ) -> NormalizedMarketData | None:
        if not isinstance(row, dict):
            return None
        pid = str(row.get("provider_instrument_id", "") or "").strip()
        if not pid:
            return None
        try:
            price = float(row.get("price", 0.0) or 0.0)
        except (TypeError, ValueError):
            return None
        if price <= 0:
            return None
        outcome = self._mappings.resolve_provider_instrument(provider, pid)
        if outcome.status != MAPPED or outcome.provider_instrument is None:
            with self._lock:
                key = f"{provider}:{pid}"
                self._dropped_unmapped[key] = self._dropped_unmapped.get(key, 0) + 1
            return None
        record = outcome.provider_instrument
        canonical_id = record.canonical_instrument_id
        with self._lock:
            sub = self._subs.get(canonical_id)
            if sub is None or sub.active_provider != provider:
                return None  # never merge a non-active source
            seq_key = (provider, pid)
            seq = row.get("seq", row.get("provider_seq"))
            if isinstance(seq, bool):
                seq = None
            if isinstance(seq, int) and seq >= 0:
                if seq <= self._last_seq.get(seq_key, -1):
                    return None  # duplicate or older provider sequence
                self._last_seq[seq_key] = seq
                provider_seq: int | None = seq
            else:
                provider_seq = None
            event_iso = self._event_iso(row.get("event_time"), received_iso)
            volume = _safe_float(row.get("volume"))
            bid = _safe_optional_float(row.get("bid"))
            ask = _safe_optional_float(row.get("ask"))
            if sub.last_event_iso and event_iso < sub.last_event_iso:
                return None  # clearly older data never overwrites newer
            if event_iso == sub.last_event_iso and (
                price,
                volume,
                bid,
                ask,
            ) == (sub.last_price, sub.last_volume, sub.last_bid, sub.last_ask):
                return None  # identical retransmission, not a new fact
            sub.router_seq += 1
            sub.last_tick_at = now
            sub.last_event_iso = event_iso
            sub.last_price = price
            sub.last_volume = volume
            sub.last_bid = bid
            sub.last_ask = ask
            first_tick = not sub.confirmed
            sub.confirmed = True
            sub.last_error = ""
            sub.stale_reported = False
            pending = sub.pending_completion
            sub.pending_completion = ""
            if pending == FAILBACK_COMPLETED:
                sub.failed_over = False
                sub.primary_healthy_since = None
            if first_tick:
                self._record(SUBSCRIPTION_CONFIRMED, canonical_id, provider, event_iso)
                self._record(DATA_RECEIVED, canonical_id, provider, event_iso)
                if pending:
                    self._record(pending, canonical_id, provider, event_iso)
            instrument = get_instrument_registry().get(canonical_id)
        if instrument is None:  # pragma: no cover — mapping implies canonical
            return None
        try:
            return NormalizedMarketData(
                instrument_id=canonical_id,
                exchange=instrument.exchange,
                segment=instrument.segment,
                symbol=instrument.symbol,
                event_time=event_iso,
                received_time=received_iso,
                last_price=price,
                volume=volume,
                bid=bid,
                ask=ask,
                provider=provider,
                provider_seq=provider_seq,
                router_seq=sub.router_seq,
            )
        except ValueError:
            return None

    def _event_iso(self, raw: Any, received_iso: str) -> str:
        """Provider stamp when sane, else receipt time (future never trusted)."""
        if isinstance(raw, str) and raw.strip():
            try:
                claimed = datetime.datetime.fromisoformat(raw.strip())
                if claimed <= datetime.datetime.fromisoformat(received_iso):
                    return raw.strip()
            except (TypeError, ValueError):
                pass
        return received_iso

    def _observe_primary(self, sub: _Subscription, now: float) -> bool:
        """Maintain the failback hysteresis window for a failed-over sub.

        Returns True while the primary is currently usable. The healthy
        window starts on first observation (PRIMARY_RECOVERED) and resets
        on any unhealthy sighting — failback needs a FULL continuous
        window, never a single lucky poll.
        """
        usable, _ = self._mapping_usable(sub.canonical_id, self._config.primary)
        feed_usable, _ = self._feed_usable(self._config.primary)
        if usable and feed_usable:
            if sub.primary_healthy_since is None:
                sub.primary_healthy_since = now
                self._record(PRIMARY_RECOVERED, sub.canonical_id, self._config.primary, "watching")
            return True
        sub.primary_healthy_since = None
        return False

    def _primary_hysteresis_met(self, sub: _Subscription, now: float) -> bool:
        """True once the primary has been healthy for the full window."""
        since = sub.primary_healthy_since
        return since is not None and now - since >= self._config.recovery_window_s

    def _evaluate_subscription(self, sub: _Subscription, now: float) -> None:
        current = sub.active_provider
        if current:
            usable, _ = self._mapping_usable(sub.canonical_id, current)
            feed_usable, _ = self._feed_usable(current)
            fresh = (
                sub.last_tick_at is not None
                and now - sub.last_tick_at <= self._config.freshness_threshold_s
            )
            if usable and feed_usable and (fresh or not sub.confirmed):
                self._watch_primary_recovery(sub, now)
                return
            if sub.confirmed and not fresh and not sub.stale_reported:
                # Stale transition is journaled once (flag clears on the
                # next accepted tick) — never silently continued.
                sub.stale_reported = True
                self._record(DATA_STALE, sub.canonical_id, current, "tick age exceeded")
            # Failover scan: the primary rejoins the candidates ONLY after
            # its hysteresis window (stickiness — no flap-back on a blip).
            if sub.failed_over and current != self._config.primary:
                self._observe_primary(sub, now)
            for provider in self._config.ordered_providers:
                if provider == current:
                    continue
                if (
                    provider == self._config.primary
                    and sub.failed_over
                    and not self._primary_hysteresis_met(sub, now)
                ):
                    continue
                usable_next, _ = self._mapping_usable(sub.canonical_id, provider)
                feed_usable_next, _ = self._feed_usable(provider)
                if usable_next and feed_usable_next:
                    back_to_primary = provider == self._config.primary and sub.failed_over
                    self._switch_provider(
                        sub, provider, FAILBACK_COMPLETED if back_to_primary else FAILOVER_STARTED
                    )
                    sub.failed_over = True
                    sub.primary_healthy_since = None
                    return
            # Nothing usable: safe no-data state (recoverable on next sweep).
            if current and (not usable or not feed_usable):
                self._unsubscribe_quietly(current, sub.canonical_id)
                sub.active_provider = ""
                sub.confirmed = False
                self._record(
                    NO_DATA_AVAILABLE,
                    sub.canonical_id,
                    "",
                    self._unavailable_reason(sub.canonical_id),
                )
            return
        # No active provider: (re)scan in policy order, primary first.
        provider = self._first_usable_provider(sub.canonical_id)
        if provider:
            recovered = sub.failed_over and provider == self._config.primary
            self._switch_provider(
                sub, provider, PRIMARY_RECOVERED if recovered else SUBSCRIPTION_STARTED
            )
            if recovered:
                # Failback completes on the first fresh tick (not at switch:
                # RESUME is data, and failed_over clears with it).
                sub.primary_healthy_since = None

    def _watch_primary_recovery(self, sub: _Subscription, now: float) -> None:
        """Hysteresis: fail back only after a full healthy window."""
        if not sub.failed_over or sub.active_provider == self._config.primary:
            sub.primary_healthy_since = None
            return
        if not self._observe_primary(sub, now):
            return
        if self._primary_hysteresis_met(sub, now):
            # failed_over clears on the first fresh tick (completion), so
            # the switch-to-tick gap still reads FAILOVER, never READY.
            self._switch_provider(sub, self._config.primary, FAILBACK_COMPLETED)
            sub.primary_healthy_since = None

    def _readiness(self, sub: _Subscription, now: float) -> tuple[str, str]:
        if self._feed_error.get(sub.active_provider):
            return READINESS_ERROR, self._feed_error[sub.active_provider]
        if not sub.active_provider:
            return UNAVAILABLE, self._unavailable_reason(sub.canonical_id)
        if not sub.confirmed:
            if sub.failed_over:
                return FAILOVER, f"switched to {sub.active_provider}, awaiting first tick"
            return WAITING_FOR_DATA, f"subscribed via {sub.active_provider}, awaiting first tick"
        fresh = (
            sub.last_tick_at is not None
            and now - sub.last_tick_at <= self._config.freshness_threshold_s
        )
        if fresh:
            return READY, f"live via {sub.active_provider}"
        if sub.failed_over:
            return FAILOVER, f"stale on {sub.active_provider}"
        return STALE, f"no fresh tick via {sub.active_provider}"


def _safe_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _safe_optional_float(value: Any) -> float | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


__all__ = ["MarketDataRouter", "MarketDataFeed", "RouterError"]
