"""Strategy library layout tests (legacy top-level vs colocated)."""

from strategy.language.storage import (
    list_strategies,
    load_strategy,
    save_strategy,
    strategy_dir,
)


def test_legacy_layout_top_level_py_used_as_is(tmp_path) -> None:
    """Pre-v1.11 workstation layout: ``*.py`` directly in the folder."""
    (tmp_path / "OBR.py").write_text("strategy('OBR')\n", encoding="utf-8")
    assert strategy_dir(tmp_path) == tmp_path
    assert list_strategies(tmp_path) == ["OBR"]
    assert load_strategy("OBR", tmp_path) == "strategy('OBR')\n"


def test_data_root_layout_colocates_strategies_subdir(tmp_path) -> None:
    """Empty data root: library lives in ``<root>/strategies``."""
    assert strategy_dir(tmp_path) == tmp_path / "strategies"
    save_strategy("strategy('A')\n", "A", tmp_path)
    assert list_strategies(tmp_path) == ["A"]
