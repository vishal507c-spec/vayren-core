"""Compiler — Python-native Strategy compilation (no DSL, no IR, no VM)."""

from collections.abc import Iterable
from dataclasses import dataclass

from strategy.models.parameters import StrategyParameters
from strategy.runtime import StrategyLogic


class StrategyLanguageError(Exception):
    def __init__(self, errors: list):
        self.errors = errors
        super().__init__("\n".join(str(e) for e in errors))


@dataclass
class CompiledStrategy:
    code: str
    strategy_class: type
    param_specs: tuple
    param_defaults: dict[str, float]

    def create_logic(
        self,
        params: StrategyParameters,
        owner_id: str | None = None,
    ) -> StrategyLogic:
        try:
            logic = self.strategy_class(params)
        except Exception as e:
            raise StrategyLanguageError([f"Failed to create strategy logic: {e}"]) from e
        # Owner identity feeds PlotEvent.source_strategy (strategy-owned
        # visual ownership); failures never break strategy construction.
        if owner_id:
            try:
                setter = getattr(logic, "set_owner_id", None)
                if callable(setter):
                    setter(str(owner_id))
                else:
                    logic._owner_id = str(owner_id)  # type: ignore[attr-defined]
            except Exception:
                pass
        return logic


def compile_strategy(code: str) -> CompiledStrategy:
    # Expect Python code defining a class Strategy(PythonStrategy) or similar
    namespace: dict = {"__builtins__": __builtins__}
    try:
        exec(code, namespace)
    except SyntaxError as e:
        raise StrategyLanguageError([f"Syntax error at line {e.lineno}: {e.msg}"]) from e
    except Exception as e:
        raise StrategyLanguageError([f"Compilation failed: {e}"]) from e

    # Find StrategyLogic subclass (skip the base class itself)
    strategy_class = None
    for name, obj in namespace.items():
        if (
            isinstance(obj, type)
            and (hasattr(obj, "on_bar_logic") or hasattr(obj, "on_bar"))
            and name != "PythonStrategy"
        ):
            strategy_class = obj
            break
    if (
        strategy_class is None
        and "Strategy" in namespace
        and isinstance(namespace["Strategy"], type)
    ):
        # Fallback: look for class named Strategy
        strategy_class = namespace["Strategy"]
    if strategy_class is None:
        raise StrategyLanguageError(
            ["No Strategy class found — define class Strategy(PythonStrategy) with on_bar_logic"]
        )

    # Extract param specs if available
    param_specs: tuple = ()
    try:
        if hasattr(strategy_class, "param_specs") and callable(strategy_class.param_specs):
            specs = strategy_class.param_specs()
            if isinstance(specs, Iterable):
                param_specs = tuple(specs)
    except Exception:
        param_specs = ()

    param_defaults = {}
    for spec in param_specs:
        try:
            param_defaults[spec.label] = float(spec.default)
            param_defaults[spec.key] = float(spec.default)
        except Exception:
            pass

    return CompiledStrategy(
        code=code,
        strategy_class=strategy_class,
        param_specs=param_specs,
        param_defaults=param_defaults,
    )
