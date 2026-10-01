"""Compiler — Python-native Strategy compilation (no DSL, no IR, no VM)."""

import ast
import builtins as _builtins_module
from collections.abc import Iterable
from dataclasses import dataclass

from strategy.models.parameters import StrategyParameters
from strategy.runtime import StrategyLogic

_real_import = _builtins_module.__import__


def _guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
    """`__import__` replacement enforcing the module allowlist at runtime."""
    root = (name or "").split(".")[0]
    if root not in _ALLOWED_IMPORT_ROOTS:
        raise ImportError(f"import of {name!r} is not allowed in strategy code")
    return _real_import(name, globals, locals, fromlist, level)


_SAFE_BUILTINS = {
    "__import__": _guarded_import,
    "__build_class__": _builtins_module.__build_class__,
    "abs": abs,
    "min": min,
    "max": max,
    "sum": sum,
    "len": len,
    "range": range,
    "round": round,
    "float": float,
    "int": int,
    "bool": bool,
    "str": str,
    "list": list,
    "dict": dict,
    "tuple": tuple,
    "set": set,
    "frozenset": frozenset,
    "enumerate": enumerate,
    "zip": zip,
    "sorted": sorted,
    "reversed": reversed,
    "any": any,
    "all": all,
    "iter": iter,
    "next": next,
    "isinstance": isinstance,
    "issubclass": issubclass,
    "hasattr": hasattr,
    "getattr": getattr,
    "callable": callable,
    "super": super,
    "type": type,
    "property": property,
    "staticmethod": staticmethod,
    "classmethod": classmethod,
    "slice": slice,
    "repr": repr,
    "pow": pow,
    "divmod": divmod,
    "ValueError": ValueError,
    "TypeError": TypeError,
    "KeyError": KeyError,
    "IndexError": IndexError,
    "AttributeError": AttributeError,
    "RuntimeError": RuntimeError,
    "StopIteration": StopIteration,
    "Exception": Exception,
    "NotImplemented": NotImplemented,
    "True": True,
    "False": False,
    "None": None,
}

_BLOCKED_NODES = (
    ast.Global,
    ast.Delete,
)

#: Modules strategy code may import. Everything else (os/sys/subprocess/
#: socket/pathlib/shutil/importlib/ctypes/...) is rejected — user code must
#: never touch the filesystem, the network or the interpreter internals.
#: `strategy.*` stays importable: the builtins subclass it.
_ALLOWED_IMPORT_ROOTS = frozenset(
    {
        "__future__",
        "strategy",
        "collections",
        "dataclasses",
        "datetime",
        "decimal",
        "enum",
        "functools",
        "itertools",
        "math",
        "statistics",
        "typing",
        "zoneinfo",  # PEP 615 tz data only (no IO/clock); OBR stamps IST-aware bars.
    }
)


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
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        raise StrategyLanguageError([f"Syntax error at line {e.lineno}: {e.msg}"]) from e
    for node in ast.walk(tree):
        if isinstance(node, _BLOCKED_NODES):
            raise StrategyLanguageError(["globals and deletes are not allowed in strategy code"])
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            roots = (
                [a.name.split(".")[0] for a in node.names]
                if isinstance(node, ast.Import)
                else [(node.module or "").split(".")[0]]
            )
            if any(root not in _ALLOWED_IMPORT_ROOTS for root in roots):
                raise StrategyLanguageError(
                    [f"import of {roots!r} is not allowed in strategy code"]
                )
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in ("eval", "exec", "compile", "open")
        ):
            raise StrategyLanguageError([f"{node.func.id}() is not allowed in strategy code"])
    namespace: dict = {"__builtins__": _SAFE_BUILTINS, "__name__": "strategy"}
    try:
        exec(code, namespace)
    except SyntaxError as e:
        raise StrategyLanguageError([f"Syntax error at line {e.lineno}: {e.msg}"]) from e
    except Exception as e:
        raise StrategyLanguageError([f"Compilation failed: {e}"]) from e

    # Find StrategyLogic subclass defined in this code (skip imports and base).
    # exec'd classes carry __module__ == "strategy" (our __name__); imported
    # helpers carry their home module and are skipped.
    # `StrategyLogic` is a non-runtime-checkable Protocol, so `issubclass` is
    # not a legal runtime check against it. The MRO walk is the honest test: a
    # real subclass names the protocol in its bases, a duck-typed class does
    # not (and the hasattr protocol check above is the documented fallback).
    logic_bases = tuple(getattr(StrategyLogic, "__mro__", ()))
    strategy_class = None
    candidates = []
    for name, obj in namespace.items():
        if (
            isinstance(obj, type)
            and (hasattr(obj, "on_bar_logic") or hasattr(obj, "on_bar"))
            and name != "PythonStrategy"
            and getattr(obj, "__module__", None) == "strategy"
        ):
            if not any(base in obj.__mro__ for base in logic_bases):
                continue
            candidates.append((name, obj))
    if len(candidates) == 1:
        strategy_class = candidates[0][1]
    elif len(candidates) > 1:
        names = sorted(name for name, _ in candidates)
        raise StrategyLanguageError(
            [f"Multiple Strategy classes found ({', '.join(names)}) — define exactly one"]
        )
    if strategy_class is None:
        # Fallback: class named Strategy with the bar protocol.
        candidate = namespace.get("Strategy")
        if isinstance(candidate, type) and (
            hasattr(candidate, "on_bar_logic") or hasattr(candidate, "on_bar")
        ):
            strategy_class = candidate
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
