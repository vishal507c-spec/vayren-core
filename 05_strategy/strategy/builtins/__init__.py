"""Built-in strategy implementations."""

from strategy.builtins.obr import KIND as OBR_KIND
from strategy.builtins.obr import PARAM_SPECS as OBR_SPECS
from strategy.builtins.obr import obr_factory
from strategy.builtins.obr_sell import KIND as OBR_SELL_KIND
from strategy.builtins.obr_sell import PARAM_SPECS as OBR_SELL_SPECS
from strategy.builtins.obr_sell import obr_sell_factory
from strategy.builtins.sma_crossover import KIND as SMA_CROSSOVER_KIND
from strategy.builtins.sma_crossover import PARAM_SPECS as SMA_CROSSOVER_SPECS
from strategy.builtins.sma_crossover import sma_crossover_factory
from strategy.models.definition import StrategyDefinition
from strategy.models.parameters import StrategyParameters  # noqa: F401
from strategy.registry import StrategyRegistry

__all__ = [
    "SMA_CROSSOVER_KIND",
    "SMA_CROSSOVER_SPECS",
    "sma_crossover_factory",
    "OBR_SELL_KIND",
    "OBR_SELL_SPECS",
    "obr_sell_factory",
    "OBR_KIND",
    "OBR_SPECS",
    "obr_factory",
    "install_builtins",
    "default_definitions",
]


def install_builtins(registry: StrategyRegistry) -> None:
    registry.register_kind(SMA_CROSSOVER_KIND, sma_crossover_factory, SMA_CROSSOVER_SPECS)
    registry.register_kind(OBR_SELL_KIND, obr_sell_factory, OBR_SELL_SPECS)
    registry.register_kind(OBR_KIND, obr_factory, OBR_SPECS)


def default_definitions() -> tuple[StrategyDefinition, ...]:
    return (
        StrategyDefinition(
            id="obr-sell",
            name="OBR SELL",
            version="1.0",
            kind=OBR_SELL_KIND,
            params=StrategyParameters.from_specs(OBR_SELL_SPECS),
            allocation_pct=100.0,
        ),
        StrategyDefinition(
            id="obr",
            name="OBR",
            version="1.0",
            kind=OBR_KIND,
            params=StrategyParameters.from_specs(OBR_SPECS),
            allocation_pct=100.0,
        ),
    )
