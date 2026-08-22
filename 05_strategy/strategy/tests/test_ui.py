"""Strategy UI smoke tests."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from strategy.builtins import SMA_CROSSOVER_KIND, SMA_CROSSOVER_SPECS
from strategy.models.definition import StrategyDefinition
from strategy.models.parameters import StrategyParameters
from strategy.ui.control_panel import StrategyControlPanel
from strategy.ui.strategy_list_panel import StrategyListPanel


def _qt_app() -> QApplication:
    inst = QApplication.instance()
    if isinstance(inst, QApplication):
        return inst
    return QApplication([])


def test_control_panel_set_strategies():
    app = _qt_app()
    assert app is not None
    panel = StrategyControlPanel()
    defs = (
        StrategyDefinition(
            id="a",
            name="A",
            version="1.0",
            kind=SMA_CROSSOVER_KIND,
            params=StrategyParameters.from_specs(SMA_CROSSOVER_SPECS),
        ),
        StrategyDefinition(
            id="b",
            name="B",
            version="2.0",
            kind=SMA_CROSSOVER_KIND,
            params=StrategyParameters.from_specs(SMA_CROSSOVER_SPECS),
        ),
    )
    panel.set_strategies(defs)
    assert panel.active_strategy_id == "a"
    panel.set_timeframes(("15m", "1h"))
    assert panel.timeframe == "15m"
    panel.select_timeframe("1h")
    assert panel.timeframe == "1h"


def test_control_busy_disables_run():
    app = _qt_app()
    assert app is not None
    panel = StrategyControlPanel()
    panel.set_strategies(
        (
            StrategyDefinition(
                id="a",
                name="A",
                version="1.0",
                kind=SMA_CROSSOVER_KIND,
                params=StrategyParameters.from_specs(SMA_CROSSOVER_SPECS),
            ),
        )
    )
    panel.set_timeframes(("15m",))
    assert panel._run_button.isEnabled()
    panel.set_busy(True)
    assert not panel._run_button.isEnabled()
    panel.set_busy(False)
    assert panel._run_button.isEnabled()


def test_strategy_list_panel():
    app = _qt_app()
    assert app is not None
    panel = StrategyListPanel()
    defs = (
        StrategyDefinition(
            id="a",
            name="A",
            version="1.0",
            kind=SMA_CROSSOVER_KIND,
            params=StrategyParameters.from_specs(SMA_CROSSOVER_SPECS),
            allocation_pct=40,
        ),
    )
    panel.set_strategies(defs)
    assert len(panel.definitions) == 1
    panel.set_strategies(())
    assert len(panel.definitions) == 0
