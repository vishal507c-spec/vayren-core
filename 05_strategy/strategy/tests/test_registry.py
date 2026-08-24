"""Strategy registry tests — generic, no builtin dependency."""

import pytest

from strategy.models.definition import StrategyDefinition
from strategy.models.parameters import ParameterSpec, StrategyParameters
from strategy.models.signal import Signal
from strategy.registry import StrategyRegistry, StrategyRegistryError
from strategy.runtime import BarView

DUMMY_KIND = "dummy_test_kind"
DUMMY_SPECS: tuple[ParameterSpec, ...] = (
    ParameterSpec(key="p1", label="P1", default=1, minimum=0, maximum=10, decimals=0),
)


class _DummyLogic:
    def warmup(self) -> int:
        return 0

    def on_bar(self, view: BarView) -> Signal | None:  # noqa: ARG002
        return None


def _dummy_factory(params: StrategyParameters) -> _DummyLogic:  # noqa: ARG001
    return _DummyLogic()


def _registry() -> StrategyRegistry:
    r = StrategyRegistry()
    r.register_kind(DUMMY_KIND, _dummy_factory, DUMMY_SPECS)
    return r


def _def(_registry: StrategyRegistry, sid: str = "test-one") -> StrategyDefinition:
    return StrategyDefinition(
        id=sid,
        name="Test",
        version="1.0",
        kind=DUMMY_KIND,
        params=StrategyParameters.from_specs(DUMMY_SPECS),
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
        kind=DUMMY_KIND,
        params=StrategyParameters.from_specs(DUMMY_SPECS),
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
    assert DUMMY_KIND in r.kinds
    # Registry starts empty if not populated
    empty = StrategyRegistry()
    assert empty.kinds == ()
