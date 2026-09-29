"""AI provider boundary — VAYREN never depends on a specific model.

Providers are pluggable (Provider A, Provider B, Local Model, Future
Model). The default offline provider means the deterministic runtime keeps
working with no AI at all: availability failures are graceful.
"""

from abc import ABC, abstractmethod
from threading import Lock


class AiProvider(ABC):
    """Boundary of an AI provider. Core code never calls a specific model."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Stable identifier of this provider."""

    @abstractmethod
    def available(self) -> bool:
        """True when the provider can currently serve requests."""

    @abstractmethod
    def describe(self) -> str:
        """Human/AI readable description of this provider."""


class OfflineProvider(AiProvider):
    """Default provider: AI is unavailable, the runtime keeps working."""

    @property
    def name(self) -> str:
        return "offline"

    def available(self) -> bool:
        return False

    def describe(self) -> str:
        return "no AI provider configured; the deterministic VAYREN runtime remains authoritative"


class AiProviderRegistry:
    """Registers AI providers and selects the first available one."""

    def __init__(self) -> None:
        self._providers: dict[str, AiProvider] = {"offline": OfflineProvider()}
        self._lock = Lock()

    def register(self, provider: AiProvider) -> None:
        """Register a provider; duplicate names are rejected."""
        with self._lock:
            if provider.name in self._providers:
                msg = f"provider already registered: {provider.name}"
                raise ValueError(msg)
            self._providers[provider.name] = provider

    def providers(self) -> tuple[AiProvider, ...]:
        """All registered providers, ordered by name."""
        with self._lock:
            names = sorted(self._providers)
            providers = tuple(self._providers[name] for name in names)
        return providers

    def available(self) -> tuple[AiProvider, ...]:
        """Providers currently available, ordered by name."""
        return tuple(provider for provider in self.providers() if provider.available())

    def select(self, preferred: str | None = None) -> AiProvider | None:
        """Return the preferred available provider, else the first available,
        else None. Failures are graceful: callers handle None.

        A preferred provider that is registered but currently unavailable
        falls back to the first available provider (it does not return None
        while another provider could serve); an unknown preferred name behaves
        like no preference.
        """
        with self._lock:
            providers = dict(self._providers)
        if preferred is not None and preferred in providers:
            provider = providers[preferred]
            if provider.available():
                return provider
        available = tuple(provider for provider in self.providers() if provider.available())
        return available[0] if available else None

    def status(self) -> str:
        """Deterministic status report of every provider."""
        lines = [f"ai providers: {len(self._providers)}"]
        for provider in self.providers():
            state = "available" if provider.available() else "unavailable"
            lines.append(f"- {provider.name}: {state} | {provider.describe()}")
        return "\n".join(lines)

    def __contains__(self, name: str) -> bool:
        return name in self._providers

    def __len__(self) -> int:
        return len(self._providers)
