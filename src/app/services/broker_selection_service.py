"""BrokerSelectionService — the application's authoritative broker selection
(Phase 21 M4; design §8: one selection, read by every surface).

Owns the in-memory ``BrokerSelection`` + its ``FileSelectionStore``. Every
broker-facing surface (historical download, LIVE workspace, CLI, status
panels) reads THE selection through this service — none keeps its own
broker state.

Selection states (design §3 of the M4 mission — never conflated):

- **explicit** — a user choice (CLI ``--broker`` or the UI selector),
  persisted with reason ``"user-selected"``;
- **compatibility default** — no selection file exists: the historical
  default broker is recorded as the selection with reason
  ``"compatibility-default (no explicit selection yet)"`` so behavior is
  unchanged while remaining visible/auditable;
- **none/unconfigured** — surfaces that need a capability the selected
  broker lacks get a fail-closed reason via :func:`surface_resolution`
  (trading additionally keeps the existing PAPER downgrade path).

Fail-closed: unknown broker name, capability mismatch and corrupt store
content raise :class:`SelectionError`/:class:`SelectionLoadError` — the
previous valid selection is preserved, never silently replaced.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from broker.capabilities import CapabilityStatus, Domain
from broker.providers.zerodha import BROKER_ID as ZERODHA_BROKER_ID
from broker.registry import BrokerRegistry, default_registry
from broker.selection import (
    BrokerSelection,
    MemorySelectionStore,
    SelectionError,
    SelectionStore,
    surface_resolution,
    surface_status,
)
from broker.selection_store import FileSelectionStore, SelectionLoadError
from broker.vocab import BrokerNotRegisteredError, Environment

DEFAULT_HISTORY_BROKER = ZERODHA_BROKER_ID
_USER_REASON = "user-selected"
_COMPAT_REASON = "compatibility-default (no explicit selection yet)"


@dataclass(frozen=True)
class SelectionOutcome:
    """Result of one selection change (for UI/CLI surfacing)."""

    selection: BrokerSelection
    persisted: bool
    capabilities: tuple[str, ...]
    domains: tuple[str, ...]


class BrokerSelectionService:
    """Single authoritative selection for one application run."""

    def __init__(
        self,
        store: SelectionStore,
        registry: BrokerRegistry | None = None,
    ) -> None:
        self._store = store
        self._registry = registry if registry is not None else default_registry()
        self._selection: BrokerSelection | None = None
        self._loaded_from_store = False

    # ── resolution ────────────────────────────────────────────────────────

    def current(self) -> BrokerSelection:
        """The authoritative selection, establishing the default if needed.

        Order: explicit CLI/UI selection → persisted store → compatibility
        default. Every path is recorded via the selection ``reason``.
        """
        if self._selection is not None:
            return self._selection
        persisted = self._load_store()
        if persisted is not None:
            self._selection = persisted
            return self._selection
        self._selection = self._compatibility_default()
        return self._selection

    def current_or_none(self) -> BrokerSelection | None:
        """Like :meth:`current` but None when nothing can be established."""
        try:
            return self.current()
        except (SelectionError, SelectionLoadError):
            return None

    @property
    def loaded_from_store(self) -> bool:
        """True when the current selection came from persisted state."""
        return self._loaded_from_store

    def _load_store(self) -> BrokerSelection | None:
        """Read the store once per service lifetime; corrupt files raise."""
        try:
            persisted = self._store.load()
        except SelectionLoadError:
            self._loaded_from_store = False
            raise
        self._loaded_from_store = persisted is not None
        return persisted

    def _compatibility_default(self) -> BrokerSelection:
        """First-run default: the first CONFIGURED venue, recorded openly.

        The old default hardcoded Zerodha and only looked elsewhere when
        Zerodha had no stored config, which silently handed a Zerodha-shaped
        session to an operator who had actually configured FYERS. Now the
        registry is walked and the first venue with stored credentials wins;
        with nothing configured the historical provider is used only as a
        last resort, and the provenance stays visible in ``reason``.
        """
        configured: str | None = None
        try:
            store_path = getattr(self._store, "path", None)
            if store_path is not None:
                data_dir = Path(store_path).parent
                from broker.common.credentials_store import default_store

                cred_store = default_store(data_dir)
                for reg_name in self._registry.names():
                    if cred_store.load(f"{reg_name}_config") or cred_store.load(reg_name):
                        configured = reg_name
                        break
        except Exception:
            configured = None
        chosen = configured or DEFAULT_HISTORY_BROKER
        if chosen not in self._registry:
            raise SelectionError(f"compatibility default broker {chosen!r} is not registered")
        return BrokerSelection(
            name=chosen,
            environment=Environment.PAPER,
            selected_at=datetime.now().astimezone().isoformat(timespec="seconds"),
            reason=_COMPAT_REASON,
        )

    # ── mutation ──────────────────────────────────────────────────────────

    def select(
        self,
        name: str,
        *,
        environment: Environment | None = None,
        session_running: bool = False,
    ) -> SelectionOutcome:
        """Make ``name`` the authoritative selection (explicit user choice).

        Fail-closed: unknown broker → SelectionError; the previous valid
        selection is preserved. The validated selection is persisted.

        ``session_running`` is the caller's guard: a selection may NOT change
        while a trading session is live, because the venue was resolved into
        the session at build time — swapping the selection mid-run would
        leave orders, capital and market data pointing at two different
        venues at once. The refusal is explicit, never a silent switch.
        """
        if name not in self._registry:
            raise SelectionError(f"unknown broker {name!r} — registered: {self._registry.names()}")
        record = self._registry.get(name)
        env = environment
        if env is None:
            env = Environment.SANDBOX if Domain.TRADING in record.faces else Environment.PAPER
        selection = BrokerSelection(
            name=name,
            environment=env,
            selected_at=datetime.now().astimezone().isoformat(timespec="seconds"),
            reason=_USER_REASON,
        )
        if session_running:
            raise SelectionError(
                f"cannot switch broker to {name!r} while a trading session is running — "
                "stop the session first (the active venue cannot change mid-run)"
            )
        self._store.save(selection)
        self._selection = selection
        self._loaded_from_store = False
        return SelectionOutcome(
            selection=selection,
            persisted=True,
            capabilities=tuple(sorted(record.capabilities.items)),
            domains=tuple(d.value for d in record.faces),
        )

    def reload(self) -> BrokerSelection | None:
        """Re-read the store (external change); None when nothing persisted."""
        self._selection = None
        self._loaded_from_store = False
        try:
            return self.current()
        except (SelectionError, SelectionLoadError):
            return None

    # ── surface queries (the only way surfaces ask) ───────────────────────

    def surface_allowed(self, domain: Domain) -> tuple[bool, str]:
        """Pure capability check for a surface against the current selection."""
        selection = self.current()
        try:
            record = self._registry.get(selection.name)
        except BrokerNotRegisteredError:
            return False, f"broker {selection.name!r} is not registered"
        return surface_resolution(selection, record.capabilities, domain)

    def surface_status(self, domain: Domain) -> tuple[CapabilityStatus, str]:
        """Tri-state surface check (M8 §3): SUPPORTED / NOT_SUPPORTED / NOT_CONFIGURED.

        Bool-compatible behavior is preserved via :meth:`surface_allowed`;
        this is the explicit declaration for UI/gates (never inferred).
        """
        selection = self.current()
        try:
            record = self._registry.get(selection.name)
        except BrokerNotRegisteredError:
            return CapabilityStatus.NOT_CONFIGURED, (f"broker {selection.name!r} is not registered")
        return surface_status(selection, record.capabilities, domain)

    def broker_capabilities(self, name: str | None = None) -> dict[str, object]:
        """Capability facts for one broker (UI list; never fake availability)."""
        target = name or self.current().name
        if target not in self._registry:
            raise SelectionError(
                f"unknown broker {target!r} — registered: {self._registry.names()}"
            )
        record = self._registry.get(target)
        domains = {d.value: record.has_domain(d) for d in Domain}
        return {
            "name": record.name,
            "display_name": record.display_name,
            "domains": domains,
            "capabilities": tuple(sorted(record.capabilities.items)),
        }

    def broker_choices(self) -> tuple[dict[str, object], ...]:
        """Registry-backed choice list for the UI selector (name-sorted)."""
        choices: list[dict[str, object]] = []
        for record in self._registry.list():
            domains = {d.value: record.has_domain(d) for d in Domain}
            choices.append(
                {
                    "name": record.name,
                    "display_name": record.display_name,
                    "domains": domains,
                    "capabilities": tuple(sorted(record.capabilities.items)),
                }
            )
        return tuple(choices)


def app_selection_store(data_dir: str | Path) -> FileSelectionStore:
    """Canonical selection-file location: ``<data_dir>/broker_selection.json``."""
    return FileSelectionStore(Path(data_dir) / "broker_selection.json")


__all__ = [
    "DEFAULT_HISTORY_BROKER",
    "BrokerSelectionService",
    "MemorySelectionStore",
    "SelectionOutcome",
    "app_selection_store",
]
