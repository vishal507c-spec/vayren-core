"""Compiler — validated AST -> StrategyIR -> Universal VM (only execution path)."""

import ast
from dataclasses import dataclass

from strategy.language.ir import StrategyIR, build_ir
from strategy.language.parser import CompileError, parse_and_validate
from strategy.models.parameters import StrategyParameters
from strategy.runtime import StrategyLogic


class StrategyLanguageError(Exception):
    def __init__(self, errors: list[CompileError]):
        self.errors = errors
        super().__init__("\n".join(e.pretty() for e in errors))


@dataclass
class CompiledStrategy:
    tree: ast.Module
    param_defaults: dict[str, float]  # label -> default
    code: str
    ir: StrategyIR | None = None
    warmup: int = 20

    def create_logic(
        self, params: StrategyParameters, owner_id: str | None = None
    ) -> StrategyLogic:
        """Create VM logic — IR → Universal VM is the ONLY strategy execution path.

        No exec fallback. If IR is missing or VM fails, fail loudly.
        """
        if self.ir is None:
            raise StrategyLanguageError(
                [CompileError(line=1, col=0, message="IR not available — compilation failed")]
            )
        from strategy.vm import vm_from_ir

        # Fail loudly — no silent fallback to another implementation
        return vm_from_ir(self.ir, params, owner_id=owner_id)


def compile_strategy(code: str) -> CompiledStrategy:
    tree, errors, param_infos = parse_and_validate(code)
    if errors:
        raise StrategyLanguageError(errors)
    if tree is None:
        raise StrategyLanguageError([e for e in errors])  # noqa: C416
    defaults: dict[str, float] = {}
    for p in param_infos:
        defaults[p.label] = float(p.default)
    ir = build_ir(code, tree, param_infos)
    return CompiledStrategy(tree=tree, param_defaults=defaults, code=code, ir=ir)


def compile_to_ir(code: str) -> StrategyIR:
    """Compile source to generic IR — no execution, deterministic."""
    tree, errors, param_infos = parse_and_validate(code)
    if errors:
        raise StrategyLanguageError(errors)
    if tree is None:
        raise StrategyLanguageError([CompileError(line=1, col=0, message="Empty source")])
    return build_ir(code, tree, param_infos)
