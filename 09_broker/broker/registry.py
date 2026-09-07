"""BrokerRegistry — the ONLY name→plugin map in VAYREN (design §4.4, M2).

Registration is explicit (bootstrap-only composition; no scanning, no
reflection). Duplicate names are rejected; unknown names fail closed.
``find_with``/``find_with_domain`` are the authoritative capability
discovery paths.
"""

from __future__ import annotations

from broker.capabilities import CapabilitySet, Domain
from broker.faces import PluginLike
from broker.vocab import (
    BrokerError,
    BrokerNotRegisteredError,
    ErrorCode,
)


class DuplicateBrokerError(BrokerError):
    """A broker name was registered twice (registration is fail-closed)."""

    def __init__(self, name: str) -> None:
        super().__init__(f"broker {name!r} is already registered", ErrorCode.INVALID_REQUEST)


class BrokerRecord:
    """One registered venue: identity + advertised capabilities.

    Not a frozen dataclass because ``plugin`` is any BrokerPlugin-shaped
    object injected by the composition root (structural, no import).
    """

    __slots__ = ("name", "display_name", "plugin", "capabilities", "faces")

    def __init__(
        self,
        name: str,
        display_name: str,
        plugin: PluginLike,
        capabilities: CapabilitySet,
        faces: tuple[Domain, ...],
    ) -> None:
        if not name:
            raise ValueError("broker name must be non-empty")
        if not display_name:
            raise ValueError("broker display_name must be non-empty")
        if not faces:
            raise ValueError(f"broker {name!r} must serve at least one domain")
        self.name = name
        self.display_name = display_name
        self.plugin = plugin
        self.capabilities = capabilities
        self.faces = faces

    def has_domain(self, domain: Domain) -> bool:
        """True when the broker serves ``domain`` (capability-aware UI)."""
        return domain in self.faces

    def __repr__(self) -> str:
        domains = ",".join(d.value for d in self.faces)
        return (
            f"BrokerRecord(name={self.name!r}, display_name={self.display_name!r}, "
            f"domains=({domains}), capabilities={len(self.capabilities.items)})"
        )


class BrokerRegistry:
    """Single source of registered brokers. Registration order independent."""

    def __init__(self) -> None:
        self._records: dict[str, BrokerRecord] = {}

    def register(self, record: BrokerRecord) -> None:
        """Register one venue; a duplicate name raises DuplicateBrokerError."""
        if record.name in self._records:
            raise DuplicateBrokerError(record.name)
        self._records[record.name] = record

    def unregister(self, name: str) -> None:
        """Remove a registration; unknown names fail closed."""
        if name not in self._records:
            raise BrokerNotRegisteredError(
                f"cannot unregister unknown broker {name!r} — registered: {sorted(self._records)}"
            )
        del self._records[name]

    def get(self, name: str) -> BrokerRecord:
        """Resolve a name; unknown → BrokerNotRegisteredError (fail-closed)."""
        record = self._records.get(name)
        if record is None:
            raise BrokerNotRegisteredError(
                f"no broker registered under {name!r} — registered: {sorted(self._records)}"
            )
        return record

    def list(self) -> tuple[BrokerRecord, ...]:
        """All records, name-sorted (deterministic for UI/tests)."""
        return tuple(self._records[name] for name in sorted(self._records))

    def names(self) -> tuple[str, ...]:
        """Registered names, sorted."""
        return tuple(sorted(self._records))

    def find_with(self, cap: str) -> tuple[BrokerRecord, ...]:
        """Every broker advertising the exact capability id."""
        return tuple(record for record in self.list() if record.capabilities.supports(cap))

    def find_with_domain(self, domain: Domain) -> tuple[BrokerRecord, ...]:
        """Every broker serving the domain."""
        return tuple(record for record in self.list() if domain in record.faces)

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and name in self._records

    def __iter__(self):  # type: ignore[no-untyped-def]
        return iter(self._records)

    def __len__(self) -> int:
        return len(self._records)


_default_registry: BrokerRegistry | None = None


def default_registry() -> BrokerRegistry:
    """Process-wide registry (lazy). Composition roots and shims use this."""
    global _default_registry
    if _default_registry is None:
        _default_registry = BrokerRegistry()
    return _default_registry
