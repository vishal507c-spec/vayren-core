"""Production wiring: snapshots, candidate promotion into the repo, restore.

Every production write is preceded by a hash-pinned snapshot under the
unit's evidence dir. Any gate failure after wiring restores the snapshots
byte-for-byte before the failure is reported — the repository is never left
half-migrated.
"""

from __future__ import annotations

import ast
import json
import time
from pathlib import Path

from ..config import EVIDENCE_DIR, RETENTION_PATH, ROOT
from ..hashing import sha256_file
from ..redact import redact_text

RISK_RS = ROOT / "rust" / "vayren-core" / "src" / "risk.rs"
LIB_RS = ROOT / "rust" / "vayren-core" / "src" / "lib.rs"
BRIDGE_PY = ROOT / "07_risk" / "risk" / "native_checks.py"
ENGINE_PY = ROOT / "07_risk" / "risk" / "engine.py"
ORACLE_DIR = ROOT / "scripts" / "migration" / "agent" / "oracles"
PARITY_TEST = ROOT / "07_risk" / "risk" / "tests" / "test_risk_kernel_parity.py"


def _stamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def evidence_dir(unit_id: str) -> Path:
    path = EVIDENCE_DIR / unit_id.replace(".", "_") / "agent"
    path.mkdir(parents=True, exist_ok=True)
    return path


def snapshot(unit_id: str, files: list[Path]) -> dict[str, str]:
    """Copy production files into evidence; return {rel: sha256}."""
    dest = evidence_dir(unit_id) / "pre_wire"
    dest.mkdir(parents=True, exist_ok=True)
    hashes: dict[str, str] = {}
    for path in files:
        rel = path.relative_to(ROOT).as_posix()
        hashes[rel] = sha256_file(path)
        if path.is_file():
            backup = dest / rel.replace("/", "__")
            backup.write_bytes(path.read_bytes())
    (dest / "manifest.json").write_text(
        redact_text(json.dumps({"at": _stamp(), "hashes": hashes}, indent=2)) + "\n",
        encoding="utf-8",
    )
    return hashes


def restore(unit_id: str) -> list[str]:
    """Restore pre-wire snapshots byte-for-byte; return restored rel paths."""
    dest = evidence_dir(unit_id) / "pre_wire"
    manifest_path = dest / "manifest.json"
    if not manifest_path.is_file():
        return []
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    restored: list[str] = []
    for rel in manifest.get("hashes", {}):
        backup = dest / rel.replace("/", "__")
        target = ROOT / rel
        if backup.is_file():
            target.write_bytes(backup.read_bytes())
            restored.append(rel)
        elif target.is_file() and manifest["hashes"][rel] == "missing":
            target.unlink()
            restored.append(rel + " (removed)")
    return restored


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")


def patch_lib_rs(spec_dict: dict, source_text: str | None = None) -> str:
    """Return the new lib.rs with the risk module + FFI (verified by caller)."""
    text = source_text if source_text is not None else LIB_RS.read_text(encoding="utf-8")
    if "pub mod risk;" in text:
        raise ValueError("lib.rs already wires the risk module")
    text = text.replace("pub mod order_state;\n", "pub mod order_state;\npub mod risk;\n", 1)
    f64 = [e["name"] for e in spec_dict["inputs"] if e["type"] == "f64"]
    ints = [e["name"] for e in spec_dict["inputs"] if e["type"] == "int"]
    bools = [e["name"] for e in spec_dict["inputs"] if e["type"] == "bool"]
    assigns: list[str] = []

    def _assign_line(name: str, rhs: str) -> None:
        line = f"        let {name} = {rhs};"
        if len(line) <= 100:
            assigns.append(line)
            return
        assigns.append(f"        let {name} =")
        assigns.append(f"            {rhs};")

    for index, name in enumerate(f64):
        _assign_line(name, f"f64_values.get({index}).copied().unwrap_or(0.0)")
    for index, name in enumerate(ints):
        _assign_line(name, f"i64_values.get({index}).copied().unwrap_or(0)")
    for index, name in enumerate(bools):
        _assign_line(name, f"i64_values.get({len(ints) + index}).copied().unwrap_or(0) != 0")
    ffi = "\n// ── risk policy kernel (agent-migrated) ───────────────────────────────\n\n"
    ffi += "/// Evaluate pure scalar risk checks. Returns the check bitmask.\n"
    ffi += "/// Wrong-length inputs fail closed (mask 0 = deny); panics are caught.\n"
    ffi += "#[no_mangle]\n"
    ffi += 'pub unsafe extern "C" fn vy_risk_kernel(\n'
    ffi += "    f64_values: *const f64,\n"
    ffi += "    f64_len: usize,\n"
    ffi += "    i64_values: *const i64,\n"
    ffi += "    i64_len: usize,\n"
    ffi += ") -> u32 {\n"
    ffi += "    let result = std::panic::catch_unwind(|| {\n"
    ffi += (
        f"        if f64_len != {len(f64)} || i64_len != {len(ints) + len(bools)} {{\n"
        "            return 0u32;\n"
        "        }\n"
    )
    ffi += "        let f64_values = slice(f64_values, f64_len);\n"
    ffi += "        let i64_values = slice(i64_values, i64_len);\n"
    ffi += "\n".join(assigns) + "\n"
    ffi += "        let inputs = risk::RiskKernelInputs {\n"
    for entry in spec_dict["inputs"]:
        ffi += f"            {entry['name']},\n"
    ffi += "        };\n"
    ffi += "        risk::evaluate_risk_kernel(&inputs)\n"
    ffi += "    });\n"
    ffi += "    result.unwrap_or(0)\n"
    ffi += "}\n"
    if not text.endswith("\n"):
        text += "\n"
    return text + ffi


def _pack_line(entry: dict) -> str:
    name, type_, source = entry["name"], entry["type"], entry["source"]
    zero = {"f64": "0.0", "int": "0", "bool": "False"}[type_]
    coerce = {"f64": "float", "int": "int", "bool": "bool"}[type_]
    if source == "request.side == 'BUY'":
        expr = 'request.side == "BUY"'
    elif source.startswith("request.") and source.endswith(" is not None"):
        expr = f"request.{source[len('request.') : -len(' is not None')]} is not None"
    elif source.startswith("policy.") and source.endswith(" is not None"):
        expr = f"policy.{source[len('policy.') : -len(' is not None')]} is not None"
    elif source.startswith("request."):
        expr = f"request.{source[len('request.') :]}"
    elif source.startswith("policy."):
        expr = f"policy.{source[len('policy.') :]}"
    else:
        raise ValueError(f"unknown input source: {source}")
    if type_ == "bool":
        return f'    env["{name}"] = bool(\n        {expr}\n    )'
    return (
        f'    env["{name}"] = (\n'
        f"        {coerce}({expr}) if ({expr}) is not None\n"
        f"        else {zero}\n"
        "    )"
    )


def _tuple_block(const: str, names: list[str]) -> list[str]:
    if not names:
        return [f"{const}: tuple[str, ...] = ()"]
    lines = [f"{const}: tuple[str, ...] = ("]
    lines.extend(f'    "{name}",' for name in names)
    lines.append(")")
    return lines


def render_bridge(spec_dict: dict, check_names: list[str]) -> str:
    """Render the ctypes bridge (production file, generated constants)."""
    f64 = [e["name"] for e in spec_dict["inputs"] if e["type"] == "f64"]
    ints = [e["name"] for e in spec_dict["inputs"] if e["type"] == "int"]
    bools = [e["name"] for e in spec_dict["inputs"] if e["type"] == "bool"]
    bits = "\n".join(f'    "{name}",' for name in check_names)
    consts = "\n".join(f"BIT_{name.upper()} = 1 << {bit}" for bit, name in enumerate(check_names))
    pack = "\n".join(_pack_line(e) for e in spec_dict["inputs"])
    const_list = ",\n ".join(f'"BIT_{name.upper()}"' for name in check_names)
    all_block = (
        '__all__ = [\n "F64_ORDER",\n "I64_ORDER",\n "BOOL_ORDER",\n "CHECK_NAMES",\n '
        + const_list
        + ",\n"
        + ' "pack_env",\n "check_mask",\n "check_mask_for",\n]'
    )
    return "\n".join(
        [
            '"""Rust-backed risk policy kernel (agent-migrated behavioral slice).',
            "",
            "Pure scalar checks live in Rust (`rust/vayren-core`, `risk` module);",
            "string-keyed audit reasons, kill-switch/session/clock/instrument/",
            "duplicate orchestration stays Python. Bit i of the mask = checks[i].",
            '"""',
            "",
            "from __future__ import annotations",
            "",
            "import ctypes",
            "from array import array",
            "from collections.abc import Mapping",
            "from typing import Any",
            "",
            "from core.native.loader import load_vayren_core",
            "",
            "from risk.models import RiskPolicy, RiskRequest",
            "",
            "_lib = load_vayren_core()",
            "",
        ]
        + _tuple_block("F64_ORDER", f64)
        + _tuple_block("I64_ORDER", ints)
        + _tuple_block("BOOL_ORDER", bools)
        + [
            "",
            "CHECK_NAMES: tuple[str, ...] = (",
            bits,
            ")",
            "",
            consts,
            "",
            "",
            "def pack_env(policy: RiskPolicy, request: RiskRequest) -> dict[str, Any]:",
            '    """Kernel env in spec order (None-safe: guarded by present flags)."""',
            "    env: dict[str, Any] = {}",
            pack,
            "    return env",
            "",
            "",
            "def _f64_view(values: object) -> tuple[ctypes.Array, array]:",
            '    packed = array("d", values)  # type: ignore[arg-type]',
            "    return (ctypes.c_double * len(packed)).from_buffer(packed), packed",
            "",
            "",
            "def _i64_view(values: object) -> tuple[ctypes.Array, array]:",
            '    packed = array("q", values)  # type: ignore[arg-type]',
            "    return (ctypes.c_int64 * len(packed)).from_buffer(packed), packed",
            "",
            "",
            "def check_mask(env: Mapping[str, Any]) -> int:",
            '    """Kernel bitmask for a packed env (see spec input order)."""',
            "    f64_values = [float(env[name]) for name in F64_ORDER]",
            "    i64_values = [int(env[name]) for name in I64_ORDER]",
            "    i64_values += [1 if env[name] else 0 for name in BOOL_ORDER]",
            "    f64_view, _keep_f = _f64_view(f64_values)",
            "    i64_view, _keep_i = _i64_view(i64_values)",
            "    _lib.vy_risk_kernel.restype = ctypes.c_uint32",
            "    _lib.vy_risk_kernel.argtypes = [",
            "        ctypes.POINTER(ctypes.c_double),",
            "        ctypes.c_size_t,",
            "        ctypes.POINTER(ctypes.c_int64),",
            "        ctypes.c_size_t,",
            "    ]",
            "    return int(",
            "        _lib.vy_risk_kernel(f64_view, len(f64_values), i64_view, len(i64_values))",
            "    )",
            "",
            "",
            "def check_mask_for(policy: RiskPolicy, request: RiskRequest) -> int:",
            '    """Pack the kernel env and evaluate the Rust kernel (fail-closed)."""',
            "    return check_mask(pack_env(policy, request))",
            "",
            "",
            all_block,
            "",
        ]
    )


def _is_if_pattern(stmt: ast.If, kernel_names: list[str]) -> tuple[str, str] | None:
    """Match `if COND: ok &= check(name, False, DETAIL) else: check(name, True)`."""
    if len(stmt.body) != 1 or len(stmt.orelse) != 1:
        return None
    body, other = stmt.body[0], stmt.orelse[0]

    def _call_of(node: ast.stmt) -> ast.Call | None:
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            return node.value
        if (
            isinstance(node, ast.AugAssign)
            and isinstance(node.op, ast.BitAnd)
            and isinstance(node.value, ast.Call)
        ):
            return node.value
        return None

    body_call, other_call = _call_of(body), _call_of(other)
    if body_call is None or other_call is None:
        return None
    for call_node, want in ((body_call, False), (other_call, True)):
        if getattr(call_node.func, "attr", "") != "_check" or len(call_node.args) < 3:
            return None
        name = call_node.args[1]
        pred = call_node.args[2]
        if not (isinstance(name, ast.Constant) and name.value in kernel_names):
            return None
        if not (isinstance(pred, ast.Constant) and pred.value is want):
            return None
    name_arg = body_call.args[1]
    assert isinstance(name_arg, ast.Constant) and isinstance(name_arg.value, str)
    name = name_arg.value
    detail_node = body_call.args[3] if len(body_call.args) > 3 else None
    if isinstance(detail_node, ast.Constant) and isinstance(detail_node.value, str):
        import json as _json

        detail_src = _json.dumps(detail_node.value)
    elif detail_node is not None:
        detail_src = ast.unparse(detail_node)
    else:
        detail_src = '""'
    return name, detail_src


def patch_engine(source_text: str, kernel_names: list[str], bit_names: list[str]) -> str:
    """Route scalar predicates through the kernel mask; details untouched."""
    bit_of = dict(zip(kernel_names, bit_names, strict=True))
    lines = source_text.splitlines(keepends=True)
    tree = ast.parse(source_text)
    evaluate = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_evaluate":
            evaluate = node
            break
    if evaluate is None:
        raise ValueError("_evaluate not found")
    edits: list[tuple[tuple[int, int], tuple[int, int], str]] = []
    collapsed: list[tuple[int, int]] = []
    # 1. Whole-statement if-patterns (sanity, capital).
    for stmt in evaluate.body:
        if isinstance(stmt, ast.If):
            matched = _is_if_pattern(stmt, kernel_names)
            if matched is not None:
                name, detail_src = matched
                bit = bit_of[name]
                assert stmt.end_lineno is not None
                end_col = len(lines[stmt.end_lineno - 1].rstrip("\n"))
                collapsed.append((stmt.lineno, stmt.end_lineno))
                replacement = (
                    f'ok &= self._check(\n            checks,\n            "{name}",\n'
                    f"            bool(_risk_mask & {bit}),\n"
                    f'            "" if bool(_risk_mask & {bit}) else {detail_src},\n'
                    "        )"
                )
                edits.append(
                    (
                        (stmt.lineno, stmt.col_offset),
                        (stmt.end_lineno, end_col),
                        replacement,
                    )
                )
    # 2. Direct `ok &= self._check(checks, name, PRED, ...)` predicates
    # (skipping regions collapsed wholesale in step 1).
    for node in ast.walk(evaluate):
        if (
            isinstance(node, ast.AugAssign)
            and isinstance(node.op, ast.BitAnd)
            and isinstance(node.value, ast.Call)
            and not any(top <= node.lineno <= bottom for top, bottom in collapsed)
        ):
            call = node.value
            if getattr(call.func, "attr", "") == "_check" and len(call.args) >= 3:
                name_arg = call.args[1]
                if isinstance(name_arg, ast.Constant) and name_arg.value in kernel_names:
                    pred = call.args[2]
                    edits.append(
                        (
                            (pred.lineno, pred.col_offset),
                            (pred.end_lineno or pred.lineno, pred.end_col_offset or 0),
                            f"bool(_risk_mask & {bit_of[name_arg.value]})",
                        )
                    )
    for start, end, replacement in sorted(edits, reverse=True):
        _splice(lines, start, end, replacement)
    # Re-split: multi-line replacements are single list elements until now.
    lines = "".join(lines).splitlines(keepends=True)
    # Canonical reflow: any spliced check statement still over the limit is
    # rebuilt one-arg-per-line (ruff-format canonical).
    text = "".join(lines)
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_evaluate":
            evaluate = node
            break
    assert evaluate is not None
    rebuilds: list[tuple[tuple[int, int], tuple[int, int], str]] = []
    for stmt in evaluate.body:
        span = list(_iter_check_stmts(stmt, collapsed))
        for target, name, detail_node in span:
            first, last = target.lineno, target.end_lineno or target.lineno
            width = max(len(lines[i]) for i in range(first - 1, last))
            if width > 100 and "_risk_mask & BIT_" in "".join(lines[first - 1 : last]):
                indent = lines[first - 1][: len(lines[first - 1]) - len(lines[first - 1].lstrip())]
                last_len = len(lines[last - 1].rstrip("\n"))
                rebuilds.append(
                    (
                        (first, 0),
                        (last, last_len),
                        _canonical_check(
                            indent,
                            name,
                            f"bool(_risk_mask & {bit_of[name]})",
                            _detail_src(detail_node),
                        ),
                    )
                )
    for start, end, replacement in sorted(rebuilds, reverse=True):
        _splice(lines, start, end, replacement)
    lines = "".join(lines).splitlines(keepends=True)
    text = "".join(lines)
    # 3. Drop aliases consumed by the kernel (verified dead afterwards).
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_evaluate":
            evaluate = node
            break
    assert evaluate is not None
    loads: set[str] = set()
    for node in ast.walk(evaluate):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            loads.add(node.id)
    drop: list[tuple[int, int]] = []
    for stmt in evaluate.body:
        if (
            isinstance(stmt, ast.Assign)
            and len(stmt.targets) == 1
            and isinstance(stmt.targets[0], ast.Name)
            and stmt.targets[0].id
            in (
                "notional",
                "direction",
                "new_position",
                "exposure",
                "fresh",
                "tight",
                "cooled",
                "under",
            )
            and stmt.targets[0].id not in loads
        ):
            drop.append((stmt.lineno, stmt.end_lineno or stmt.lineno))
    for start, end in sorted(drop, reverse=True):
        del lines[start - 1 : end]
    text = "".join(lines)
    tree = ast.parse(text)
    # 4. Mask computation after `ok = True`.
    anchor = "\n        ok = True\n"
    if anchor not in text:
        raise ValueError("anchor `ok = True` not found")
    text = text.replace(
        anchor,
        "\n        ok = True\n        _risk_mask = _risk_check_mask_for(policy, request)\n",
        1,
    )
    # 5. Bridge import in sorted position (isort: native_checks < session).
    old_head = "from risk.session import SessionRules, clock_sane, within_session\n"
    if old_head not in text:
        raise ValueError("engine import anchor not found")
    import_block = "from risk.native_checks import (\n"
    for bit in sorted(bit_names):
        import_block += f"    {bit},\n"
    import_block += ")\n"
    import_block += "from risk.native_checks import check_mask_for as _risk_check_mask_for\n"
    text = text.replace(old_head, import_block + old_head, 1)
    ast.parse(text)
    return text


def _detail_src(node: ast.AST | None) -> str:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        import json as _json

        return _json.dumps(node.value)
    if node is not None:
        return ast.unparse(node)
    return '""'


def _canonical_check(indent: str, name: str, predicate: str, detail: str) -> str:
    cont = indent + "    "
    return (
        f"{indent}ok &= self._check(\n"
        f"{cont}checks,\n"
        f'{cont}"{name}",\n'
        f"{cont}{predicate},\n"
        f"{cont}{detail},\n"
        f"{indent})"
    )


def verify_engine_shape(source_text: str, check_order: list[str]) -> bool:
    """True when _evaluate holds every check exactly in contract order."""
    try:
        tree = ast.parse(source_text)
    except SyntaxError:
        return False
    evaluate = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_evaluate":
            evaluate = node
            break
    if evaluate is None:
        return False
    positioned: list[tuple[int, int, str]] = []
    for node in ast.walk(evaluate):
        if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "_check":
            name = node.args[1] if len(node.args) > 1 else None
            if isinstance(name, ast.Constant) and isinstance(name.value, str):
                positioned.append((node.lineno, node.col_offset, name.value))
    positioned.sort()
    firsts: list[str] = []
    for _, _, name in positioned:
        if name not in firsts:
            firsts.append(name)
    return firsts == check_order


def _iter_check_stmts(stmt: ast.stmt, collapsed: list[tuple[int, int]]):  # type: ignore[no-untyped-def]
    """Yield (AugAssign, check name, detail node) for direct check statements."""
    if isinstance(stmt, ast.If):
        span = (stmt.lineno, stmt.end_lineno or stmt.lineno)
        if span in collapsed:
            return
        for sub in (*stmt.body, *stmt.orelse):
            yield from _iter_check_stmts(sub, collapsed)
        return
    if (
        isinstance(stmt, ast.AugAssign)
        and isinstance(stmt.op, ast.BitAnd)
        and isinstance(stmt.value, ast.Call)
        and getattr(stmt.value.func, "attr", "") == "_check"
        and len(stmt.value.args) >= 4
    ):
        name_arg = stmt.value.args[1]
        if isinstance(name_arg, ast.Constant) and isinstance(name_arg.value, str):
            yield stmt, name_arg.value, stmt.value.args[3]


def _splice(
    lines: list[str], start: tuple[int, int], end: tuple[int, int], replacement: str
) -> None:
    (start_line, start_col) = start
    (end_line, end_col) = end or start
    if start_line == end_line:
        lines[start_line - 1] = (
            lines[start_line - 1][:start_col] + replacement + lines[start_line - 1][end_col:]
        )
        return
    head = lines[start_line - 1][:start_col]
    tail = lines[end_line - 1][end_col:]
    lines[start_line - 1 : end_line] = [head + replacement + tail]


def add_retention_entry(rel: str, entry: dict) -> None:
    data = json.loads(RETENTION_PATH.read_text(encoding="utf-8"))
    files = data.setdefault("files", {})
    files[rel] = entry
    # ensure_ascii=False: preserve the file's existing byte style so the
    # diff stays limited to the added entry.
    payload = json.dumps(data, indent=1, ensure_ascii=False) + "\n"
    RETENTION_PATH.write_text(payload, encoding="utf-8")


__all__ = [
    "RISK_RS",
    "LIB_RS",
    "BRIDGE_PY",
    "ENGINE_PY",
    "ORACLE_DIR",
    "PARITY_TEST",
    "evidence_dir",
    "snapshot",
    "restore",
    "write_text",
    "patch_lib_rs",
    "render_bridge",
    "patch_engine",
    "add_retention_entry",
]
