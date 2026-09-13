"""Sandbox analysis: contract, purity and migratability from real source AST.

The analyzer never guesses. It parses the unit's Python source, extracts the
check-level contract, proves which predicates are pure scalar logic over the
frozen dataclasses, and refuses — with recorded reasons — anything that is
Slint/UI, IO-bound, stateful, big-bang-exempt glue, or otherwise outside the
autonomous kernel pattern.
"""

from __future__ import annotations

import ast
import json

from ..config import RETENTION_PATH, ROOT
from ..registry import unit_by_id
from .models import AnalysisReport, KernelCheck, KernelInput, KernelSpec

NATIVE_UI_PREFIXES = (
    "04_chart/chart/widgets/",
    "04_chart/chart/windows/",
    "04_chart/chart/renderer/",
    "00_app/app/ui/",
    "02_data/data/ui/",
    "06_backtest/backtest/ui/",
    "rust/vayren-shell/",
)

QT_MARKERS = ("PySide6", "PyQt5", "PyQt6", "slint", "QRect", "QWidget", "QPainter")

IO_MODULES = (
    "threading",
    "socket",
    "sqlite3",
    "selectors",
    "subprocess",
    "asyncio",
    "http",
    "urllib",
    "requests",
    "httpx",
    "websocket",
    "websockets",
)

# Intra-repo orchestration scope: venue/state/persistence-spanning sessions
# are explicit-design territory, not autonomous kernels.
SCOPE_MARKERS = (
    "broker",
    "journal",
    "checkpoint",
    "venue",
    "paper",
    "strategy_runtime",
    "pathlib",
    "persist",
)


class UnconvertibleError(Exception):
    """A predicate cannot be expressed as pure kernel logic."""


def _retention_reason(source: str) -> str:
    try:
        data = json.loads(RETENTION_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    entry = data.get("files", {}).get(source, {})
    if isinstance(entry, dict):
        return str(entry.get("reason", ""))
    return str(entry)


def _file_text(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def pristine_source(rel: str) -> str:
    """Committed HEAD bytes of a repo file — pre-wire reference for tests."""
    import subprocess

    proc = subprocess.run(
        ["git", "show", f"HEAD:{rel}"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    if proc.returncode != 0:
        raise OSError(f"git show failed for {rel}: {proc.stderr[:200]}")
    return proc.stdout


def check_slint_exclusion(source: str) -> tuple[bool, str]:
    """True when the unit is Slint/UI and must stay out of the pipeline."""
    if source.startswith(NATIVE_UI_PREFIXES):
        return True, f"native-UI surface path excluded from migration pipeline: {source}"
    try:
        text = _file_text(source)
    except OSError:
        return False, ""
    for marker in QT_MARKERS:
        if marker in text:
            return True, f"Qt/Slint-coupled implementation (marker {marker!r}) — pipeline excluded"
    return False, ""


def io_markers(source: str) -> list[str]:
    """Concrete IO/threading markers found in the unit source."""
    try:
        tree = ast.parse(_file_text(source))
    except (OSError, SyntaxError):
        return ["unparseable source"]
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in IO_MODULES:
                    found.append(f"import {alias.name}")
        elif (
            isinstance(node, ast.ImportFrom)
            and node.module
            and node.module.split(".")[0] in IO_MODULES
        ):
            found.append(f"from {node.module} import ...")
    return sorted(set(found))


def scope_markers(source: str) -> list[str]:
    """Intra-repo orchestration markers in code identifiers (not prose)."""
    try:
        tree = ast.parse(_file_text(source))
    except (OSError, SyntaxError):
        return ["unparseable source"]
    words: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                words.update(part.lower() for part in alias.name.split("."))
        elif isinstance(node, ast.ImportFrom) and node.module:
            words.update(part.lower() for part in node.module.split("."))
        elif isinstance(node, ast.Name):
            words.add(node.id.lower())
        elif isinstance(node, ast.Attribute):
            words.add(node.attr.lower())
    return sorted(set(SCOPE_MARKERS) & words)


def _field_types() -> dict[str, str]:
    """Ground kernel input types in the real frozen dataclass contracts."""
    types: dict[str, str] = {}
    for rel, cls in (
        ("07_risk/risk/models.py", "RiskPolicy"),
        ("07_risk/risk/models.py", "RiskRequest"),
    ):
        try:
            tree = ast.parse(_file_text(rel))
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == cls:
                for stmt in node.body:
                    if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                        ann = ast.unparse(stmt.annotation)
                        types[f"{cls}.{stmt.target.id}"] = ann
    return types


def _base_type(annotation: str) -> str:
    if "bool" in annotation:
        return "bool"
    if "int" in annotation and "float" not in annotation:
        return "int"
    if "float" in annotation:
        return "f64"
    return "other"


def _is_true_const(node: dict) -> bool:
    return node.get("op") == "const" and node.get("type") == "bool" and node.get("value") is True


def _lower_if(cond: dict, then: dict, else_: dict) -> dict:
    """Lower `if C { T } else { E }` to plain boolean algebra (no if-expr)."""
    if _is_true_const(else_):
        return {"op": "or", "args": [{"op": "not", "arg": cond, "type": "bool"}, then]}
    if _is_true_const(then):
        return {"op": "or", "args": [cond, else_]}
    return {
        "op": "or",
        "args": [
            {"op": "and", "args": [cond, then]},
            {"op": "and", "args": [{"op": "not", "arg": cond}, else_]},
        ],
    }


class _Extractor:
    """Converts _check predicates of RiskEngine._evaluate into a KernelSpec."""

    def __init__(self, source_text: str) -> None:
        self.tree = ast.parse(source_text)
        self.field_types = _field_types()
        self.aliases: dict[str, ast.AST] = {}
        self.inputs: dict[str, KernelInput] = {}
        self.checks: list[KernelCheck] = []
        self.orchestration: list[str] = []
        self.check_order: list[str] = []
        self.bit = 0

    # ── inputs ──────────────────────────────────────────────────────

    def _need(self, name: str, type_: str, origin: str) -> dict:
        if name not in self.inputs:
            self.inputs[name] = KernelInput(name, type_, origin)
        return {"op": "var", "name": name, "type": type_}

    def _request(self, attr: str) -> dict:
        ann = self.field_types.get(f"RiskRequest.{attr}", "")
        base = _base_type(ann)
        if base == "other":
            raise UnconvertibleError(f"request.{attr} has non-scalar contract ({ann})")
        return self._need(f"request_{attr}", base, f"request.{attr}")

    def _policy(self, attr: str) -> dict:
        ann = self.field_types.get(f"RiskPolicy.{attr}", "")
        base = _base_type(ann)
        if base == "other":
            raise UnconvertibleError(f"policy.{attr} has non-scalar contract ({ann})")
        return self._need(f"policy_{attr}", base, f"policy.{attr}")

    def _present(self, kind: str, attr: str) -> dict:
        return self._need(f"{kind}_{attr}_present", "bool", f"{kind}.{attr} is not None")

    # ── expression conversion ───────────────────────────────────────

    def convert(self, node: ast.AST) -> dict:
        if isinstance(node, ast.Name):
            if node.id in ("True", "False"):
                return {"op": "const", "type": "bool", "value": node.id == "True"}
            if node.id in self.aliases:
                return self.convert(self.aliases[node.id])
            raise UnconvertibleError(f"unbound name {node.id}")
        if isinstance(node, ast.Constant):
            value = node.value
            if isinstance(value, bool):
                return {"op": "const", "type": "bool", "value": value}
            if isinstance(value, int):
                return {"op": "const", "type": "int", "value": value}
            if isinstance(value, float):
                return {"op": "const", "type": "f64", "value": value}
            raise UnconvertibleError(f"non-numeric constant {value!r}")
        if isinstance(node, ast.Attribute):
            receiver = node.value
            if isinstance(receiver, ast.Name) and receiver.id == "request":
                return self._request(node.attr)
            if isinstance(receiver, ast.Name) and receiver.id == "policy":
                return self._policy(node.attr)
            raise UnconvertibleError(f"unsupported attribute {ast.unparse(node)}")
        if isinstance(node, ast.Compare) and len(node.ops) == 1:
            return self._convert_compare(node)
        if isinstance(node, ast.BoolOp):
            values = [self.convert(v) for v in node.values]
            op = "and" if isinstance(node.op, ast.And) else "or"
            return {"op": op, "args": values, "type": "bool"}
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            return {"op": "not", "arg": self.convert(node.operand), "type": "bool"}
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            inner = self.convert(node.operand)
            return {"op": "neg", "arg": inner, "type": inner.get("type", "f64")}
        if isinstance(node, ast.BinOp) and isinstance(
            node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)
        ):
            left = self.convert(node.left)
            right = self.convert(node.right)
            names = {ast.Add: "add", ast.Sub: "sub", ast.Mult: "mul", ast.Div: "div"}
            type_ = "f64" if "f64" in (left.get("type"), right.get("type")) else "int"
            return {"op": names[type(node.op)], "left": left, "right": right, "type": type_}
        if isinstance(node, ast.Call):
            return self._convert_call(node)
        if isinstance(node, ast.IfExp):
            then = self.convert(node.body)
            else_ = self.convert(node.orelse)
            if then.get("type") == "bool" and else_.get("type") == "bool":
                return _lower_if(self.convert(node.test), then, else_)
            return {
                "op": "if",
                "cond": self.convert(node.test),
                "then": then,
                "else": else_,
                "type": then.get("type", "bool"),
            }
        raise UnconvertibleError(
            f"unsupported syntax {type(node).__name__}: {ast.unparse(node)[:60]}"
        )

    def _convert_compare(self, node: ast.Compare) -> dict:
        op = node.ops[0]
        kinds = {
            ast.Lt: "lt",
            ast.LtE: "le",
            ast.Gt: "gt",
            ast.GtE: "ge",
            ast.Eq: "eq",
            ast.NotEq: "ne",
        }
        if isinstance(op, (ast.Is, ast.IsNot)):
            operand = node.comparators[0]
            if isinstance(operand, ast.Constant) and operand.value is None:
                return self._convert_none_check(node.left, isinstance(op, ast.IsNot))
            raise UnconvertibleError("non-None identity comparison")
        if isinstance(op, ast.In):
            raise UnconvertibleError("membership test stays in orchestration")
        if type(op) not in kinds:
            raise UnconvertibleError(f"comparison {type(op).__name__}")
        left, right = node.left, node.comparators[0]
        # request.side == "BUY" is the single sanctioned string pattern.
        for candidate, other in ((left, right), (right, left)):
            if (
                isinstance(candidate, ast.Attribute)
                and isinstance(candidate.value, ast.Name)
                and candidate.value.id == "request"
                and candidate.attr == "side"
                and isinstance(other, ast.Constant)
                and other.value == "BUY"
            ):
                var = self._need("side_is_buy", "bool", "request.side == 'BUY'")
                if isinstance(op, ast.Eq):
                    return var
                return {"op": "not", "arg": var, "type": "bool"}
        return {
            "op": "cmp",
            "cmp": kinds[type(op)],
            "left": self.convert(left),
            "right": self.convert(right),
            "type": "bool",
        }

    def _convert_none_check(self, operand: ast.AST, is_not_none: bool) -> dict:
        if isinstance(operand, ast.Attribute) and isinstance(operand.value, ast.Name):
            kind, attr = operand.value.id, operand.attr
            if kind in ("request", "policy"):
                var = self._present(kind, attr)
                if is_not_none:
                    return var
                return {"op": "not", "arg": var, "type": "bool"}
        raise UnconvertibleError("None-check on unsupported target")

    def _convert_call(self, node: ast.Call) -> dict:
        func = node.func
        if isinstance(func, ast.Name) and func.id == "abs" and len(node.args) == 1:
            inner = self.convert(node.args[0])
            return {"op": "abs", "arg": inner, "type": inner.get("type", "f64")}
        if isinstance(func, ast.Name) and func.id == "bool" and len(node.args) == 1:
            inner = self.convert(node.args[0])
            if inner.get("type") != "bool":
                raise UnconvertibleError("bool() over non-bool")
            return inner
        raise UnconvertibleError(f"call stays in orchestration: {ast.unparse(node)[:60]}")

    # ── statement walk ──────────────────────────────────────────────

    def _as_check_call(self, stmt: ast.stmt) -> tuple[str, ast.AST | None] | None:
        call = None
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
            call = stmt.value
        if (
            call is None
            and isinstance(stmt, ast.AugAssign)
            and isinstance(stmt.op, ast.BitAnd)
            and isinstance(stmt.value, ast.Call)
        ):
            call = stmt.value
        if call is None:
            return None
        func = call.func
        if not (isinstance(func, ast.Attribute) and func.attr == "_check"):
            return None
        if len(call.args) < 3:
            return None
        name_arg = call.args[1]
        if not (isinstance(name_arg, ast.Constant) and isinstance(name_arg.value, str)):
            return None
        return name_arg.value, call.args[2]

    def _call_name(self, node: ast.AST) -> str | None:
        if not isinstance(node, ast.Call):
            return None
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "_check"):
            return None
        if len(node.args) < 3:
            return None
        name_arg = node.args[1]
        if isinstance(name_arg, ast.Constant) and isinstance(name_arg.value, str):
            return name_arg.value
        return None

    def _is_check_call(self, node: ast.AST) -> tuple[str, ast.AST | None] | None:
        if isinstance(node, (ast.Expr, ast.AugAssign)):
            return self._as_check_call(node)
        return None

    def _walk(self, stmts: list[ast.stmt], guards: list[ast.AST]) -> None:
        for stmt in stmts:
            if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
                target = stmt.targets[0]
                if isinstance(target, ast.Name) and target.id not in (
                    "ok",
                    "checks",
                    "reasons",
                    "policy",
                ):
                    self.aliases[target.id] = stmt.value
                continue
            if isinstance(stmt, ast.AnnAssign):
                continue
            if isinstance(stmt, ast.If):
                self._walk_if(stmt, guards)
                continue
            found = self._is_check_call(stmt)
            if found is not None:
                name, predicate = found
                self._emit(name, predicate, guards)

    def _walk_if(self, stmt: ast.If, guards: list[ast.AST]) -> None:
        body_checks = [self._is_check_call(s) for s in stmt.body if self._is_check_call(s)]
        else_checks = [self._is_check_call(s) for s in stmt.orelse if self._is_check_call(s)]
        # Complementary constant pattern: if COND: check(name, False) else: check(name, True).
        if len(body_checks) == 1 and len(else_checks) == 1:
            body_name, body_pred = body_checks[0]  # type: ignore[misc]
            else_name, else_pred = else_checks[0]  # type: ignore[misc]
            if (
                body_name == else_name
                and isinstance(body_pred, ast.Constant)
                and isinstance(else_pred, ast.Constant)
                and body_pred.value is False
                and else_pred.value is True
            ):
                self._emit(body_name, ast.UnaryOp(op=ast.Not(), operand=stmt.test), guards)
                return
        # Guard pattern: if X is not None: <checks> else: check(name, True)/pass.
        if self._is_none_guard(stmt.test) and self._is_passthrough(stmt.orelse):
            self._walk(stmt.body, guards + [stmt.test])
            return
        # Anything else: enclosed checks stay orchestration (honest, no guess).
        for node in ast.walk(stmt):
            name = self._call_name(node)
            if name is not None and name not in self.check_order:
                self.check_order.append(name)
                self.orchestration.append(name)

    def _is_none_guard(self, test: ast.AST) -> bool:
        try:
            self._convert_none_check_inner(test)
            return True
        except UnconvertibleError:
            return False

    def _convert_none_check_inner(self, test: ast.AST) -> dict:
        if isinstance(test, ast.Compare) and len(test.ops) == 1:
            return self._convert_compare(test)
        if isinstance(test, ast.BoolOp):
            for value in test.values:
                self._convert_none_check_inner(value)
            return {}
        raise UnconvertibleError("not a none-guard")

    def _is_passthrough(self, stmts: list[ast.stmt]) -> bool:
        if not stmts:
            return True
        if len(stmts) == 1:
            found = self._is_check_call(stmts[0])
            if found is not None and isinstance(found[1], ast.Constant) and found[1].value is True:
                self.check_order.append(found[0])
                return True
        return False

    def _emit(self, name: str, predicate: ast.AST | None, guards: list[ast.AST]) -> None:
        if name not in self.check_order:
            self.check_order.append(name)
        if predicate is None:
            self.orchestration.append(name)
            return
        try:
            expr = self.convert(predicate)
            if guards:
                # None-guard `if G: check(P) else: check(True)` means
                # P-when-guarded, pass-otherwise — never `G and P`.
                parts = [self.convert(guard) for guard in guards]
                parts = [p for p in parts if not _is_true_const(p)]
                if parts:
                    cond = parts[0] if len(parts) == 1 else {"op": "and", "args": parts}
                    cond.setdefault("type", "bool")
                    expr = _lower_if(
                        cond,
                        expr,
                        {"op": "const", "type": "bool", "value": True},
                    )
            self.checks.append(KernelCheck(name, expr, self.bit))
            self.bit += 1
        except UnconvertibleError:
            if name in [c.name for c in self.checks]:
                self.checks = [c for c in self.checks if c.name != name]
            self.orchestration.append(name)

    def run(self) -> KernelSpec:
        target = None
        for node in ast.walk(self.tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_evaluate":
                target = node
                break
        if target is None:
            raise UnconvertibleError("RiskEngine._evaluate not found")
        self._walk(target.body, [])
        # policy alias resolves through the instance; rewrite to contract root.
        return KernelSpec(
            unit_id="risk.engine.evaluate",
            inputs=tuple(self.inputs.values()),
            checks=tuple(self.checks),
            orchestration=tuple(self.orchestration),
            check_order=tuple(self.check_order),
        )


def analyze(unit_id: str, source_text: str | None = None) -> AnalysisReport:
    """Analyze one unit; refuse anything outside the autonomous pattern."""
    definition = unit_by_id(unit_id)
    if definition is None:
        return AnalysisReport(unit_id, False, f"unknown unit: {unit_id}")
    source = definition.python_source

    excluded, reason = check_slint_exclusion(source)
    if excluded:
        return AnalysisReport(unit_id, False, reason, refused_slint=True)

    if source_text is None:
        try:
            source_text = _file_text(source)
        except OSError as exc:
            return AnalysisReport(unit_id, False, f"source unreadable: {exc}")
    if "_risk_mask" in source_text or "native_checks" in source_text:
        return AnalysisReport(
            unit_id, False, "already wired to the Rust kernel — run verify/promote instead"
        )

    if definition.python_glue:
        policy = _retention_reason(source) or "tracked Python glue"
        return AnalysisReport(
            unit_id, False, f"tracked Python glue — stays Python ({policy[:120]})"
        )

    markers = io_markers(source)
    if markers:
        return AnalysisReport(
            unit_id, False, f"IO/threading scope beyond autonomous kernel: {', '.join(markers)}"
        )

    scope = scope_markers(source)
    if scope:
        return AnalysisReport(
            unit_id,
            False,
            f"session/orchestration scope beyond autonomous kernel: {', '.join(scope)}",
        )

    if unit_id == "risk.engine.evaluate":
        return _analyze_risk(source_text)

    if not definition.rust_target:
        return AnalysisReport(unit_id, False, "no Rust target — needs explicit design first")

    return AnalysisReport(
        unit_id,
        False,
        "no autonomous extractor for this pattern yet — explicit design required",
    )


def _analyze_risk(source_text: str) -> AnalysisReport:
    try:
        spec = _Extractor(source_text).run()
    except (UnconvertibleError, OSError, SyntaxError) as exc:
        return AnalysisReport("risk.engine.evaluate", False, f"extraction failed: {exc}")
    if not spec.checks:
        return AnalysisReport("risk.engine.evaluate", False, "no pure kernel checks extracted")
    return AnalysisReport(
        unit_id="risk.engine.evaluate",
        migratable=True,
        reason=(
            f"{len(spec.checks)} pure scalar checks extracted, "
            f"{len(spec.orchestration)} orchestration checks stay Python"
        ),
        kernel_checks=tuple(c.name for c in spec.checks),
        orchestration_checks=tuple(spec.orchestration),
    )


def extract_risk_spec(source_text: str | None = None) -> KernelSpec:
    if source_text is None:
        source_text = _file_text("07_risk/risk/engine.py")
    return _Extractor(source_text).run()


__all__ = [
    "analyze",
    "extract_risk_spec",
    "check_slint_exclusion",
    "io_markers",
    "pristine_source",
    "KernelSpec",
    "AnalysisReport",
]
