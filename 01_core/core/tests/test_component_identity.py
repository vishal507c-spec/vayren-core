"""Component identity and version tests."""

import pytest

from core.contracts.component import (
    ComponentId,
    ComponentMetadata,
    ComponentStatus,
    ComponentVersion,
)


@pytest.mark.parametrize(
    "name",
    ["market", "chart", "a", "data_acquirer", "a1"],
)
def test_component_id_accepts_valid_names(name: str) -> None:
    component_id = ComponentId(name)
    assert component_id.name == name
    assert str(component_id) == name


@pytest.mark.parametrize(
    "name",
    ["", "Market", "market-1", "1market", "market.chart", "market/1", "with space"],
)
def test_component_id_rejects_invalid_names(name: str) -> None:
    with pytest.raises(ValueError):
        ComponentId(name)


def test_component_id_is_hashable_and_equal() -> None:
    assert ComponentId("market") == ComponentId("market")
    assert ComponentId("market") != ComponentId("chart")
    assert len({ComponentId("market"), ComponentId("market"), ComponentId("chart")}) == 2


def test_component_version_parses_valid_string() -> None:
    version = ComponentVersion.parse("1.2.3")
    assert version.major == 1
    assert version.minor == 2
    assert version.patch == 3
    assert str(version) == "1.2.3"


@pytest.mark.parametrize(
    "text",
    ["", "1", "1.2", "1.2.3.4", "1.x.3", "a.b.c"],
)
def test_component_version_rejects_invalid_strings(text: str) -> None:
    with pytest.raises(ValueError):
        ComponentVersion.parse(text)


@pytest.mark.parametrize(
    "parts",
    [(-1, 0, 0), (1, -2, 0), (1, 0, -3)],
)
def test_component_version_rejects_negative_parts(parts: tuple[int, int, int]) -> None:
    with pytest.raises(ValueError):
        ComponentVersion(parts[0], parts[1], parts[2])


def test_component_version_equality() -> None:
    assert ComponentVersion.parse("1.0.0") == ComponentVersion(1, 0, 0)


def test_component_status_has_expected_members() -> None:
    assert {status.value for status in ComponentStatus} == {
        "unknown",
        "registered",
        "started",
        "stopped",
        "failed",
    }


def test_component_metadata_defaults() -> None:
    metadata = ComponentMetadata()
    assert metadata.description == ""
    assert metadata.tags == ()
