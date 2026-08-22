"""Strategy registry tests."""

import pytest

from strategy.builtins import SMA_CROSSOVER_KIND, SMA_CROSSOVER_SPECS, install_builtins
from strategy.models.definition import StrategyDefinition
from strategy.models.parameters import StrategyParameters
from strategy.registry import StrategyRegistry, StrategyRegistryError


def _registry() -> StrategyRegistry:
    r = StrategyRegistry()
    install_builtins(r)
    return r


def _def(_registry: StrategyRegistry, sid: str = "test-one") -> StrategyDefinition:
    return StrategyDefinition(
        id=sid,
        name="Test",
        version="1.0",
        kind=SMA_CROSSOVER_KIND,
        params=StrategyParameters.from_specs(SMA_CROSSOVER_SPECS),
        allocation_pct=40.0,
    )


def test_register_and_list():
    r = _registry()
    r.register_definition(_def(r, "a"))
    r.register_definition(_def(r, "b"))
    assert [d.id for d in r.list()] == ["a", "b"]


def test_enabled_filters():
    r = _registry()
    r.register_definition(_def(r, "a"))
    disabled = StrategyDefinition(
        id="b",
        name="B",
        version="1.0",
        kind=SMA_CROSSOVER_KIND,
        params=StrategyParameters.from_specs(SMA_CROSSOVER_SPECS),
        enabled=False,
    )
    r.register_definition(disabled)
    assert [d.id for d in r.enabled()] == ["a"]


def test_set_enabled():
    r = _registry()
    r.register_definition(_def(r, "a"))
    r.set_enabled("a", False)
    assert not r.get("a").enabled
    assert r.enabled() == ()


def test_duplicate_creates_unique_id():
    r = _registry()
    r.register_definition(_def(r, "a"))
    copy = r.duplicate("a")
    assert copy.id != "a"
    assert not copy.enabled
    assert len(r.list()) == 2


def test_allocation_clamped():
    r = _registry()
    r.register_definition(_def(r, "a"))
    r.set_allocation("a", 150)
    assert r.get("a").allocation_pct == 100.0
    r.set_allocation("a", -10)
    assert r.get("a").allocation_pct == 0.0


def test_unknown_kind_rejected():
    r = StrategyRegistry()
    bad = StrategyDefinition(
        id="x",
        name="X",
        version="1.0",
        kind="unknown",
        params=StrategyParameters({}),
    )
    with pytest.raises(StrategyRegistryError):
        r.register_definition(bad)


def test_duplicate_id_rejected():
    r = _registry()
    r.register_definition(_def(r, "a"))
    with pytest.raises(ValueError):
        r.register_definition(_def(r, "a"))


def test_kinds_sorted():
    r = _registry()
    assert SMA_CROSSOVER_KIND in r.kinds
