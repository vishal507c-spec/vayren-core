"""Strategy IR — generic, deterministic Intermediate Representation.

The IR is the validated, normalized form of a strategy source. It contains
no OBR/SMA/RSI-specific structures — only generic concepts: parameters,
data requirements, and a sequence of generic statements.

Versioned and deterministic: same source + same compiler → equivalent IR.
"""

from __future__ import annotations

import ast
import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any

IR_VERSION = 1


@dataclass(frozen=True)
class IRParameter:
    """Generic parameter definition."""

    label: str
    default: float
    type: str = "float"  # generic, not OBR-specific


@dataclass(frozen=True)
class IRDataRequirement:
    """Generic data requirement (BAR, VOLUME, RSI, etc.)."""

    name: str


# ── Expression nodes (generic) ──


@dataclass(frozen=True)
class IRConstant:
    value: Any
    kind: str = "Constant"


@dataclass(frozen=True)
class IRName:
    id: str
    kind: str = "Name"


@dataclass(frozen=True)
class IRCall:
    func: str
    args: tuple[Any, ...]
    kind: str = "Call"


@dataclass(frozen=True)
class IRBinOp:
    left: Any
    op: str
    right: Any
    kind: str = "BinOp"


@dataclass(frozen=True)
class IRUnaryOp:
    op: str
    operand: Any
    kind: str = "UnaryOp"


@dataclass(frozen=True)
class IRBoolOp:
    op: str
    values: tuple[Any, ...]
    kind: str = "BoolOp"


@dataclass(frozen=True)
class IRCompare:
    left: Any
    ops: tuple[str, ...]
    comparators: tuple[Any, ...]
    kind: str = "Compare"


# ── Statement nodes (generic) ──


@dataclass(frozen=True)
class IRAssign:
    target: str
    value: Any
    kind: str = "Assign"


@dataclass(frozen=True)
class IRAugAssign:
    target: str
    op: str
    value: Any
    kind: str = "AugAssign"


@dataclass(frozen=True)
class IRExpr:
    value: Any
    kind: str = "Expr"


@dataclass(frozen=True)
class IRIf:
    test: Any
    body: tuple[Any, ...]
    orelse: tuple[Any, ...]
    kind: str = "If"


# Union for type hints
IRExprNode = IRConstant | IRName | IRCall | IRBinOp | IRUnaryOp | IRBoolOp | IRCompare
IRStatement = IRAssign | IRAugAssign | IRExpr | IRIf


@dataclass(frozen=True)
class StrategyIR:
    """Generic Strategy IR — deterministic, versioned, serializable.

    Attributes:
        ir_version: Schema version (int) — not strategy version.
        strategy_name: Name from strategy("...") or "Untitled".
        parameters: Generic ParameterDefinitions.
        data_requirements: Generic data needs (BAR, RSI, etc.).
        statements: Generic execution flow (assignments, conditions, actions).
        source_hash: SHA256 of canonical source (for determinism check).
    """

    ir_version: int
    strategy_name: str
    parameters: tuple[IRParameter, ...]
    data_requirements: tuple[str, ...]
    statements: tuple[Any, ...]
    source_hash: str

    def to_dict(self) -> dict[str, Any]:
        """Deterministic dict (sorted keys, sorted params/requirements)."""
        return {
            "ir_version": self.ir_version,
            "strategy_name": self.strategy_name,
            "parameters": sorted([asdict(p) for p in self.parameters], key=lambda x: x["label"]),
            "data_requirements": sorted(self.data_requirements),
            "statements": [self._node_to_dict(s) for s in self.statements],
            "source_hash": self.source_hash,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, ensure_ascii=False, indent=2)

    @staticmethod
    def from_dict(data: dict[str, Any]) -> StrategyIR:
        params = tuple(IRParameter(**p) for p in data.get("parameters", []))
        reqs = tuple(data.get("data_requirements", []))
        # Statements are kept as raw dicts for simplicity on deserialization
        stmts = tuple(data.get("statements", []))
        return StrategyIR(
            ir_version=int(data.get("ir_version", IR_VERSION)),
            strategy_name=str(data.get("strategy_name", "")),
            parameters=params,
            data_requirements=reqs,
            statements=stmts,  # type: ignore[arg-type]
            source_hash=str(data.get("source_hash", "")),
        )

    @staticmethod
    def _node_to_dict(node: Any) -> Any:
        if hasattr(node, "__dataclass_fields__"):
            d = asdict(node)
            # Recursively convert nested nodes
            for k, v in list(d.items()):
                if isinstance(v, tuple):
                    d[k] = tuple(
                        StrategyIR._node_to_dict(x) if hasattr(x, "__dataclass_fields__") else x
                        for x in v
                    )
                elif hasattr(v, "__dataclass_fields__"):
                    d[k] = StrategyIR._node_to_dict(v)
            return d
        if isinstance(node, tuple):
            return tuple(StrategyIR._node_to_dict(x) for x in node)
        return node

    def __hash__(self) -> int:  # type: ignore[override]
        return hash(self.to_json())

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, StrategyIR):
            return False
        return self.to_dict() == other.to_dict()


def _hash_source(source: str) -> str:
    # Canonical: stripped, normalized line endings, no trailing whitespace
    canonical = "\n".join(line.rstrip() for line in source.strip().splitlines())
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _expr_to_ir(node: ast.AST) -> Any:
    if isinstance(node, ast.Constant):
        return IRConstant(value=node.value)
    if isinstance(node, ast.Name):
        return IRName(id=node.id)
    if isinstance(node, ast.Call):
        func = (
            node.func.id
            if isinstance(node.func, ast.Name)
            else ast.unparse(node.func)
            if hasattr(ast, "unparse")
            else "unknown"
        )  # type: ignore[attr-defined]
        args = tuple(_expr_to_ir(a) for a in node.args)
        return IRCall(func=func, args=args)
    if isinstance(node, ast.BinOp):
        op = type(node.op).__name__
        return IRBinOp(left=_expr_to_ir(node.left), op=op, right=_expr_to_ir(node.right))
    if isinstance(node, ast.UnaryOp):
        return IRUnaryOp(op=type(node.op).__name__, operand=_expr_to_ir(node.operand))
    if isinstance(node, ast.BoolOp):
        return IRBoolOp(
            op=type(node.op).__name__, values=tuple(_expr_to_ir(v) for v in node.values)
        )
    if isinstance(node, ast.Compare):
        return IRCompare(
            left=_expr_to_ir(node.left),
            ops=tuple(type(o).__name__ for o in node.ops),
            comparators=tuple(_expr_to_ir(c) for c in node.comparators),
        )
    # Fallback: use unparse for unknown
    try:
        text = ast.unparse(node) if hasattr(ast, "unparse") else str(ast.dump(node))
    except Exception:
        text = str(ast.dump(node))
    return IRConstant(value=text)


def _stmt_to_ir(node: ast.AST) -> Any | None:
    if isinstance(node, ast.Assign):
        if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            return IRAssign(target=node.targets[0].id, value=_expr_to_ir(node.value))
        # Multi-target assign: take first
        if node.targets and isinstance(node.targets[0], ast.Name):
            return IRAssign(target=node.targets[0].id, value=_expr_to_ir(node.value))
        return None
    if isinstance(node, ast.AugAssign):
        if isinstance(node.target, ast.Name):
            return IRAugAssign(
                target=node.target.id, op=type(node.op).__name__, value=_expr_to_ir(node.value)
            )
        return None
    if isinstance(node, ast.Expr):
        # Could be Call like buy(), strategy(), input()
        # For IR, we keep all Expr calls except strategy/input which are metadata
        if isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name):
            fname = node.value.func.id
            if fname in ("strategy", "input"):
                return None  # metadata, not execution flow
        return IRExpr(value=_expr_to_ir(node.value))
    if isinstance(node, ast.If):
        test = _expr_to_ir(node.test)
        body = tuple(x for x in (_stmt_to_ir(n) for n in node.body) if x is not None)
        orelse = tuple(x for x in (_stmt_to_ir(n) for n in node.orelse) if x is not None)
        return IRIf(test=test, body=body, orelse=orelse)
    return None


def build_ir(
    source: str,
    tree: ast.Module,
    params: list[Any],
    strategy_name: str | None = None,
) -> StrategyIR:
    """Build generic IR from validated AST.

    Deterministic: no random, no timestamps, sorted where applicable.
    """
    # Extract strategy name if not provided
    name = strategy_name or "Untitled"
    if not strategy_name:
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "strategy"
            ):
                if (
                    node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)
                ):
                    name = node.args[0].value
                    break

    # Parameters: generic
    ir_params = tuple(
        IRParameter(label=p.label, default=float(p.default))
        for p in sorted(params, key=lambda x: x.label)  # type: ignore[attr-defined]
    )

    # Data requirements: scan for function names and variables
    reqs: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            fname = node.func.id
            if fname in ("RSI", "ATR", "SMA", "EMA", "range"):
                reqs.add(fname)
            elif fname in (
                "buy",
                "sell",
                "close_position",
                "stop_loss",
                "take_profit",
                "time_exit",
                "exit_time",
            ):
                reqs.add("ORDER")
            elif fname == "strategy":
                reqs.add("STRATEGY_META")
            elif fname == "input":
                reqs.add("PARAM")
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            if node.id in ("close", "open", "high", "low", "volume", "bar", "time"):
                reqs.add("BAR")
                if node.id == "volume":
                    reqs.add("VOLUME")
                if node.id in ("close", "open", "high", "low"):
                    reqs.add("PRICE")

    # Statements: convert top-level body, excluding strategy/input metadata
    stmts: list[Any] = []
    for node in tree.body:
        ir_node = _stmt_to_ir(node)
        if ir_node is not None:
            stmts.append(ir_node)

    return StrategyIR(
        ir_version=IR_VERSION,
        strategy_name=name,
        parameters=ir_params,
        data_requirements=tuple(sorted(reqs)),
        statements=tuple(stmts),
        source_hash=_hash_source(source),
    )
