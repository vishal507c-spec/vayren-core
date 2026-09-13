"""KernelSpec evaluation: the executable semantics of the extracted contract.

Used by the sandbox oracle and by the analyzer smoke check. One semantics,
two targets (Python oracle here, Rust via generator_rust) — differential
parity proves they agree.
"""

from __future__ import annotations


def eval_expr(node: dict, env: dict) -> object:
    op = node["op"]
    if op == "const":
        return node["value"]
    if op == "var":
        return env[node["name"]]
    if op == "not":
        return not eval_expr(node["arg"], env)
    if op == "and":
        result = True
        for arg in node["args"]:
            result = eval_expr(arg, env) and result
            if not result:
                break
        return result
    if op == "or":
        result: object = False
        for arg in node["args"]:
            result = eval_expr(arg, env) or result
            if result:
                break
        return result
    if op == "cmp":
        left, right = eval_expr(node["left"], env), eval_expr(node["right"], env)
        cmp = node["cmp"]
        assert isinstance(left, (int, float)) and isinstance(right, (int, float))
        if cmp == "lt":
            return left < right
        if cmp == "le":
            return left <= right
        if cmp == "gt":
            return left > right
        if cmp == "ge":
            return left >= right
        if cmp == "eq":
            return left == right
        return left != right
    if op in ("add", "sub", "mul", "div"):
        left, right = eval_expr(node["left"], env), eval_expr(node["right"], env)
        assert isinstance(left, (int, float)) and isinstance(right, (int, float))
        if op == "add":
            return left + right
        if op == "sub":
            return left - right
        if op == "mul":
            return left * right
        return left / right
    if op == "abs":
        value = eval_expr(node["arg"], env)
        assert isinstance(value, (int, float))
        return abs(value)
    if op == "neg":
        value = eval_expr(node["arg"], env)
        assert isinstance(value, (int, float))
        return -value
    if op == "if":
        branch = node["then"] if eval_expr(node["cond"], env) else node["else"]
        return eval_expr(branch, env)
    raise ValueError(f"unknown expr op: {op}")


def eval_kernel(spec_dict: dict, env: dict) -> int:
    """Bitmask over spec checks in order; bit i = checks[i] passed."""
    mask = 0
    for bit, check in enumerate(spec_dict["checks"]):
        if eval_expr(check["expr"], env):
            mask |= 1 << bit
    return mask


def pack_env(spec_dict: dict, policy: object, request: object) -> dict:
    """Build the kernel env from the real frozen dataclasses."""
    env: dict = {}
    policy_d = vars(policy)
    request_d = vars(request)
    for entry in spec_dict["inputs"]:
        name, source = entry["name"], entry["source"]
        if source == "request.side == 'BUY'":
            env[name] = request_d["side"] == "BUY"
        elif source.startswith("request.") and source.endswith(" is not None"):
            attr = source[len("request.") : -len(" is not None")]
            env[name] = request_d[attr] is not None
        elif source.startswith("policy.") and source.endswith(" is not None"):
            attr = source[len("policy.") : -len(" is not None")]
            env[name] = policy_d[attr] is not None
        elif source.startswith("request."):
            env[name] = request_d[source[len("request.") :]]
        elif source.startswith("policy."):
            env[name] = policy_d[source[len("policy.") :]]
        else:
            raise ValueError(f"unknown input source: {source}")
        if entry["type"] == "bool":
            env[name] = bool(env[name])
        if env[name] is None:
            # Absent nullable: guarded out by its present-flag; total default.
            env[name] = {"f64": 0.0, "int": 0, "bool": False}[entry["type"]]
    return env


__all__ = ["eval_expr", "eval_kernel", "pack_env"]
