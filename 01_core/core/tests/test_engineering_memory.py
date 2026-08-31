"""Engineering memory tests — structured, searchable engineering decisions."""

from core.ai.memory.engineering import Decision, EngineeringMemory


def test_spec_example_round_trip() -> None:
    memory = EngineeringMemory()
    entry = memory.add(
        problem="Database throughput bottleneck",
        hypothesis="Batch size increase",
        experiment=("10k", "25k", "50k"),
        change="increase batch size",
        benchmark=("rows-per-second",),
        result="25k best",
        decision=Decision.ACCEPTED,
        reason="25k batch maximizes throughput without latency regressions",
        evidence=("benchmark: 10k=900/s, 25k=1400/s, 50k=1200/s",),
    )
    assert entry.id == 1
    assert entry.decision is Decision.ACCEPTED
    assert entry.experiment == ("10k", "25k", "50k")


def test_ids_auto_increment() -> None:
    memory = EngineeringMemory()
    first = memory.add("problem one", "hypothesis one")
    second = memory.add("problem two", "hypothesis two")
    assert first.id == 1
    assert second.id == 2
    assert len(memory) == 2


def test_get_by_id_and_missing() -> None:
    memory = EngineeringMemory()
    entry = memory.add("problem", "hypothesis")
    assert memory.get(1) is entry
    try:
        memory.get(99)
    except KeyError as error:
        assert "Engineering entry not found: 99" in str(error)
    else:
        raise AssertionError("expected KeyError")


def test_search_is_case_insensitive_and_spans_fields() -> None:
    memory = EngineeringMemory()
    memory.add("Database throughput bottleneck", "Batch size increase", result="25k best")
    memory.add("Chart paint latency", "pixmap cache", result="0.93 ms")
    assert [entry.id for entry in memory.search("BOTTLENECK")] == [1]
    assert [entry.id for entry in memory.search("pixmap")] == [2]
    assert [entry.id for entry in memory.search("25k")] == [1]


def test_search_returns_deterministic_order() -> None:
    memory = EngineeringMemory()
    memory.add("problem alpha", "hypothesis")
    memory.add("problem beta", "hypothesis alpha")
    assert [entry.id for entry in memory.search("alpha")] == [1, 2]


def test_search_matches_experiment_and_evidence() -> None:
    memory = EngineeringMemory()
    memory.add(
        "throughput",
        "batch size",
        experiment=("10k", "50k"),
        evidence=("profile log",),
    )
    assert [entry.id for entry in memory.search("50k")] == [1]
    assert [entry.id for entry in memory.search("profile")] == [1]


def test_decisions_filters_deferred() -> None:
    memory = EngineeringMemory()
    memory.add("problem a", "hypothesis a", decision=Decision.ACCEPTED, reason="works")
    memory.add("problem b", "hypothesis b")
    memory.add("problem c", "hypothesis c", decision=Decision.REJECTED, reason="regression")
    assert [entry.id for entry in memory.decisions()] == [1, 3]


def test_problems_are_unique_and_ordered() -> None:
    memory = EngineeringMemory()
    memory.add("same problem", "h1")
    memory.add("other problem", "h2")
    memory.add("same problem", "h3")
    assert memory.problems() == ("same problem", "other problem")


def test_iteration_is_in_insertion_order() -> None:
    memory = EngineeringMemory()
    first = memory.add("problem a", "hypothesis a")
    second = memory.add("problem b", "hypothesis b")
    assert list(memory) == [first, second]
