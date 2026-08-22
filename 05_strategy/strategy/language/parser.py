"""Parser + Validator for VAYREN Strategy Language.

Uses Python ast for parsing, then validates against allowed primitives.
Produces exact line/col errors.
"""

import ast
from dataclasses import dataclass

from .builtins import FUNCTIONS, VARIABLES

ALLOWED_NODES = (
    ast.Module,
    ast.Assign,
    ast.AugAssign,
    ast.Expr,
    ast.If,
    ast.Name,
    ast.Constant,
    ast.BinOp,
    ast.UnaryOp,
    ast.BoolOp,
    ast.Compare,
    ast.Call,
    ast.Load,
    ast.Store,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.Mod,
    ast.Pow,
    ast.UAdd,
    ast.USub,
    ast.Not,
    ast.And,
    ast.Or,
    ast.Eq,
    ast.NotEq,
    ast.Lt,
    ast.LtE,
    ast.Gt,
    ast.GtE,
)


@dataclass
class CompileError:
    line: int
    col: int
    message: str
    hint: str = ""

    def pretty(self) -> str:
        base = f"Line {self.line}, Col {self.col}: {self.message}"
        if self.hint:
            base += f"\n  hint: {self.hint}"
        return base


@dataclass
class ParamInfo:
    label: str
    default: float
    call_line: int
    call_col: int


def parse_and_validate(code: str) -> tuple[ast.Module | None, list[CompileError], list[ParamInfo]]:
    """Parse code, return (tree, errors, params). If errors non-empty, tree is still parsed but invalid."""
    errors: list[CompileError] = []
    params: list[ParamInfo] = []
    try:
        tree = ast.parse(code, filename="<strategy>")
    except SyntaxError as e:
        errors.append(
            CompileError(
                line=e.lineno or 1,
                col=e.offset or 0,
                message=e.msg or "Syntax error",
                hint="Check indentation and brackets",
            )
        )
        return None, errors, params

    # Walk and validate
    for node in ast.walk(tree):
        if not isinstance(node, ALLOWED_NODES):
            # Allow some that appear implicitly
            if isinstance(node, (ast.keyword, ast.comprehension, ast.arguments, ast.arg)):
                continue
            # Special handling: disallow imports, loops, defs, classes
            if isinstance(
                node,
                (
                    ast.Import,
                    ast.ImportFrom,
                    ast.FunctionDef,
                    ast.AsyncFunctionDef,
                    ast.ClassDef,
                    ast.For,
                    ast.While,
                    ast.With,
                    ast.Try,
                    ast.Lambda,
                    ast.Dict,
                    ast.List,
                    ast.Tuple,
                    ast.Subscript,
                    ast.Attribute,
                    ast.Await,
                    ast.Yield,
                ),
            ):
                # Allow tuples/lists for very limited? For now allow input tuples but keep strict: disallow
                # Actually allow Tuple/List as they may appear in some contexts, but keep warning for these node types
                if isinstance(
                    node,
                    (ast.Import, ast.ImportFrom, ast.FunctionDef, ast.ClassDef, ast.For, ast.While),
                ):
                    errors.append(
                        CompileError(
                            line=getattr(node, "lineno", 1),
                            col=getattr(node, "col_offset", 0),
                            message=f"Not allowed: {type(node).__name__}",
                            hint="Only assignments, if, and trading primitives are allowed",
                        )
                    )
                    continue
            if isinstance(node, (ast.Attribute,)):
                # Disallow attribute access like foo.bar
                errors.append(
                    CompileError(
                        line=node.lineno,
                        col=node.col_offset,
                        message="Attribute access not allowed",
                        hint="Use primitives like RSI(14) not obj.attr",
                    )
                )
                continue
            # For other allowed-like nodes, skip error but keep checking calls
            if isinstance(node, (ast.Tuple, ast.List, ast.Dict, ast.keyword)):
                continue
            if isinstance(node, ast.Call):
                # check function name allowed
                if isinstance(node.func, ast.Name):
                    fname = node.func.id
                    if fname not in FUNCTIONS and fname not in {"range"}:
                        # also allow range which is in FUNCTIONS
                        errors.append(
                            CompileError(
                                line=node.lineno,
                                col=node.col_offset,
                                message=f"Unknown function: {fname}()",
                                hint=f"Allowed: {', '.join(sorted(FUNCTIONS))}",
                            )
                        )
                else:
                    errors.append(
                        CompileError(
                            line=node.lineno,
                            col=node.col_offset,
                            message="Only direct function calls allowed",
                        )
                    )
                continue
            # If still not allowed and is a major node, flag
            if isinstance(
                node,
                (
                    ast.Module,
                    ast.Assign,
                    ast.Expr,
                    ast.If,
                    ast.Name,
                    ast.Constant,
                    ast.BinOp,
                    ast.UnaryOp,
                    ast.BoolOp,
                    ast.Compare,
                    ast.Call,
                    ast.Load,
                    ast.Store,
                    ast.Add,
                    ast.Sub,
                    ast.Mult,
                    ast.Div,
                    ast.Mod,
                    ast.Pow,
                    ast.UAdd,
                    ast.USub,
                    ast.Not,
                    ast.And,
                    ast.Or,
                    ast.Eq,
                    ast.NotEq,
                    ast.Lt,
                    ast.LtE,
                    ast.Gt,
                    ast.GtE,
                ),
            ):
                continue
        # Validate calls more thoroughly
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                fname = node.func.id
                if fname == "input":
                    # expect 1-2 args, first is default number, second optional label
                    if not (1 <= len(node.args) <= 2):
                        errors.append(
                            CompileError(
                                line=node.lineno,
                                col=node.col_offset,
                                message="input() needs 1 or 2 args",
                                hint='e.g. input(1.25, "C1 Range")',
                            )
                        )
                    else:
                        # extract
                        arg0 = node.args[0]
                        if isinstance(arg0, ast.Constant) and isinstance(arg0.value, (int, float)):
                            default = float(arg0.value)
                        else:
                            errors.append(
                                CompileError(
                                    line=arg0.lineno,
                                    col=arg0.col_offset,
                                    message="input first arg must be number",
                                )
                            )
                            continue
                        label = ""
                        if len(node.args) == 2:
                            arg1 = node.args[1]
                            if isinstance(arg1, ast.Constant) and isinstance(arg1.value, str):
                                label = arg1.value
                            else:
                                label = (
                                    str(ast.unparse(arg1)) if hasattr(ast, "unparse") else "param"
                                )
                        else:
                            # infer label from assignment target if available
                            label = f"param_{len(params) + 1}"
                        params.append(
                            ParamInfo(
                                label=label or f"param_{len(params) + 1}",
                                default=default,
                                call_line=node.lineno,
                                call_col=node.col_offset,
                            )
                        )
                elif fname == "strategy":
                    if len(node.args) != 1 or not isinstance(node.args[0], ast.Constant):
                        errors.append(
                            CompileError(
                                line=node.lineno,
                                col=node.col_offset,
                                message='strategy("name") needs one string arg',
                            )
                        )
                elif fname in FUNCTIONS:
                    spec = FUNCTIONS[fname]
                    expected = spec["args"]
                    if isinstance(expected, tuple):
                        if not (expected[0] <= len(node.args) <= expected[1]):
                            errors.append(
                                CompileError(
                                    line=node.lineno,
                                    col=node.col_offset,
                                    message=f"{fname}() wrong arg count",
                                )
                            )
                    elif len(node.args) != expected:
                        errors.append(
                            CompileError(
                                line=node.lineno,
                                col=node.col_offset,
                                message=f"{fname}() expects {expected} arg(s)",
                            )
                        )
                else:
                    errors.append(
                        CompileError(
                            line=node.lineno,
                            col=node.col_offset,
                            message=f"Unknown function: {fname}()",
                            hint=f"Allowed: {', '.join(sorted(FUNCTIONS))}",
                        )
                    )
                # check variable names
        if isinstance(node, ast.Name):
            # allow if it's a target of assignment or known variable/function
            if isinstance(node.ctx, ast.Store):
                continue
            if (
                node.id not in VARIABLES
                and node.id not in FUNCTIONS
                and node.id not in {"True", "False", "None"}
            ):
                # It could be a user variable — allow any lowercase name that was assigned? For simplicity allow any Name that is not uppercase primitive mismatch
                # We will allow user variables: any name that is not disallowed is ok if it appears as assignment target elsewhere.
                # To keep validator simple, allow all stores and any load that is either variable or function — so permit any identifier as potential user var
                pass
    return tree, errors, params
