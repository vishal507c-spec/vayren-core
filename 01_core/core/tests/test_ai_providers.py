"""AI provider tests — provider-agnostic boundary, graceful unavailability."""

from core.ai.providers import AiProvider, AiProviderRegistry, OfflineProvider


class FakeProvider(AiProvider):
    """A pretend available provider for tests."""

    def __init__(self, name: str = "provider-a", available: bool = True) -> None:
        self._name = name
        self._available = available

    @property
    def name(self) -> str:
        return self._name

    def available(self) -> bool:
        return self._available

    def describe(self) -> str:
        return f"fake provider {self._name}"


def test_default_registry_has_offline_provider() -> None:
    registry = AiProviderRegistry()
    assert "offline" in registry
    assert len(registry) == 1
    assert isinstance(registry.providers()[0], OfflineProvider)


def test_offline_provider_is_unavailable() -> None:
    registry = AiProviderRegistry()
    assert registry.available() == ()
    assert registry.select() is None
    assert registry.select("offline") is None


def test_failure_is_graceful_not_an_exception() -> None:
    registry = AiProviderRegistry()
    provider = registry.select()
    assert provider is None
    status = registry.status()
    assert "offline: unavailable" in status


def test_available_provider_is_selected() -> None:
    registry = AiProviderRegistry()
    fake = FakeProvider()
    registry.register(fake)
    assert registry.select() is fake
    assert registry.select("provider-a") is fake


def test_preferred_unavailable_provider_returns_none() -> None:
    registry = AiProviderRegistry()
    registry.register(FakeProvider("provider-a", available=False))
    registry.register(FakeProvider("provider-b"))
    assert registry.select("provider-a") is None
    selected = registry.select("provider-b")
    assert selected is not None
    assert selected.name == "provider-b"


def test_duplicate_provider_is_rejected() -> None:
    registry = AiProviderRegistry()
    try:
        registry.register(OfflineProvider())
    except ValueError as error:
        assert "provider already registered: offline" in str(error)
    else:
        raise AssertionError("expected ValueError for duplicate provider")


def test_providers_are_ordered_by_name() -> None:
    registry = AiProviderRegistry()
    registry.register(FakeProvider("zeta"))
    registry.register(FakeProvider("alpha"))
    assert [provider.name for provider in registry.providers()] == ["alpha", "offline", "zeta"]
