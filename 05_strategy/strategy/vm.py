"""Universal Strategy VM — generic IR interpreter.

The VM executes StrategyIR deterministically, without knowing OBR/SMA/RSI
as strategy names. It evaluates generic expressions, maintains isolated
per-instance state, and produces generic Signals.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Any

from market.models.bar import Bar

from strategy.language.ir import (
    IRAssign,
    IRAugAssign,
    IRBinOp,
    IRBoolOp,
    IRCall,
    IRCompare,
    IRConstant,
    IRExpr,
    IRIf,
    IRName,
    IRUnaryOp,
    StrategyIR,
)
from strategy.models.parameters import StrategyParameters
from strategy.models.signal import Signal, SignalKind
from strategy.runtime import BarView, StrategyLogic


@dataclass
class VMState:
    """Per-instance runtime state — isolated per symbol/run."""

    variables: dict[str, Any]
    closes: deque[float]
    highs: deque[float]
    lows: deque[float]
    volumes: deque[int]
    pending_kind: SignalKind | None = None
    pending_sl: float | None = None
    pending_tp: float | None = None
    pending_time_exit: str | None = None


class StrategyVM:
    """Universal VM for StrategyIR — generic, deterministic, isolated.

    Implements StrategyLogic so it can be used by the existing
    StrategyRuntime/BacktestRunner without modification.
    """

    def __init__(self, ir: StrategyIR, params: StrategyParameters | dict[str, float] | None = None):
        self._ir = ir
        # Normalize params to dict label->float
        if params is None:
            self._params: dict[str, float] = {p.label: float(p.default) for p in ir.parameters}
        elif isinstance(params, StrategyParameters):
            # params may be keyed by whatever, but we treat as label->value
            self._params = {k: float(v) for k, v in dict(params).items()}
            # Also ensure defaults for missing
            for p in ir.parameters:
                if p.label not in self._params:
                    self._params[p.label] = float(p.default)
        else:
            self._params = {k: float(v) for k, v in params.items()}
            for p in ir.parameters:
                if p.label not in self._params:
                    self._params[p.label] = float(p.default)
        self._state = VMState(
            variables={},
            closes=deque(maxlen=100),
            highs=deque(maxlen=100),
            lows=deque(maxlen=100),
            volumes=deque(maxlen=100),
        )

    def warmup(self) -> int:
        return 20

    # ── StrategyLogic interface ──

    def on_bar(self, view: BarView) -> Signal | None:
        bar = view.bar
        # Update indicator history
        self._state.closes.append(bar.close)
        self._state.highs.append(bar.high)
        self._state.lows.append(bar.low)
        self._state.volumes.append(bar.volume)
        # Reset pending per bar
        self._state.pending_kind = None
        self._state.pending_sl = None
        self._state.pending_tp = None
        # Note: pending_time_exit persists across bars until triggered? In original logic it was per-bar set via time_exit() call
        # We will handle time_exit as a statement that sets pending_time_exit, then check after statements
        # For determinism, we clear only if not set by previous bar's time_exit? Actually time_exit is a statement that sets a future exit time, should persist
        # But original _CompiledLogic cleared pending_time_exit each bar before exec, then set via time_exit() call, then checked after exec
        # So we will clear before executing IR, then allow IR to set it, then check
        self._state.pending_time_exit = None

        # Build evaluation context
        ctx: dict[str, Any] = {
            "close": bar.close,
            "open": bar.open,
            "high": bar.high,
            "low": bar.low,
            "volume": bar.volume,
            "bar": bar,
            "time": bar.timestamp,
            # Variables from previous bars
            **self._state.variables,
        }

        # Execute IR statements generically
        for stmt in self._ir.statements:
            self._exec_stmt(stmt, ctx, bar, view)

        # After IR execution, check time_exit condition (generic)
        if self._state.pending_time_exit:
            try:
                hhmm = bar.timestamp[11:16]  # HH:MM
                if hhmm >= self._state.pending_time_exit:
                    if not view.state.flat:
                        self._state.pending_kind = (
                            SignalKind.BUY if view.state.side == "SHORT" else SignalKind.SELL
                        )
            except Exception:
                pass

        if self._state.pending_kind is None:
            return None

        return Signal(
            index=view.index,
            timestamp=bar.timestamp,
            kind=self._state.pending_kind,
            price=bar.close,
            stop_loss=self._state.pending_sl,
            take_profit=self._state.pending_tp,
        )

    # ── Generic IR interpretation ──

    def _exec_stmt(self, node: Any, ctx: dict[str, Any], bar: Bar, view: BarView) -> None:
        if isinstance(node, IRAssign):
            val = self._eval_expr(node.value, ctx, bar, view)
            ctx[node.target] = val
            self._state.variables[node.target] = val
        elif isinstance(node, IRAugAssign):
            # Evaluate current value
            cur = ctx.get(node.target, self._state.variables.get(node.target, 0))
            val = self._eval_expr(node.value, ctx, bar, view)
            # Apply op
            if node.op == "Add":
                res = cur + val
            elif node.op == "Sub":
                res = cur - val
            elif node.op == "Mult":
                res = cur * val
            elif node.op == "Div":
                res = cur / val if val != 0 else 0
            else:
                res = val
            ctx[node.target] = res
            self._state.variables[node.target] = res
        elif isinstance(node, IRExpr):
            # Evaluate expression for side effects (e.g., buy())
            self._eval_expr(node.value, ctx, bar, view)
        elif isinstance(node, IRIf):
            test_val = self._eval_expr(node.test, ctx, bar, view)
            # Truthiness: Python-like
            if bool(test_val):
                for sub in node.body:
                    self._exec_stmt(sub, ctx, bar, view)
            else:
                for sub in node.orelse:
                    self._exec_stmt(sub, ctx, bar, view)
        else:
            # Unknown statement type — ignore for forward compatibility
            pass

    def _eval_expr(self, node: Any, ctx: dict[str, Any], bar: Bar, view: BarView) -> Any:
        if isinstance(node, IRConstant):
            return node.value
        if isinstance(node, IRName):
            # Resolve order: variables, params via input (but input is Call, not Name), bar fields, helpers
            if node.id in ctx:
                return ctx[node.id]
            if node.id in self._state.variables:
                return self._state.variables[node.id]
            # Fallback: try to resolve as parameter label (for direct param name usage)
            if node.id in self._params:
                return self._params[node.id]
            # Unknown name → 0 for determinism (or could raise, but we return 0 to avoid crash)
            return 0
        if isinstance(node, IRCall):
            return self._eval_call(node, ctx, bar, view)
        if isinstance(node, IRBinOp):
            left = self._eval_expr(node.left, ctx, bar, view)
            right = self._eval_expr(node.right, ctx, bar, view)
            return self._eval_binop(node.op, left, right)
        if isinstance(node, IRUnaryOp):
            operand = self._eval_expr(node.operand, ctx, bar, view)
            if node.op == "Not":
                return not bool(operand)
            if node.op == "UAdd":
                return +operand
            if node.op == "USub":
                return -operand
            return operand
        if isinstance(node, IRBoolOp):
            if node.op == "And":
                result = True
                for v in node.values:
                    result = result and bool(self._eval_expr(v, ctx, bar, view))
                    if not result:
                        break
                return result
            if node.op == "Or":
                result = False
                for v in node.values:
                    result = bool(self._eval_expr(v, ctx, bar, view))
                    if result:
                        return True
                return False
            return False
        if isinstance(node, IRCompare):
            left = self._eval_expr(node.left, ctx, bar, view)
            for op, comp_node in zip(node.ops, node.comparators):
                right = self._eval_expr(comp_node, ctx, bar, view)
                if not self._eval_compare(op, left, right):
                    return False
                left = right
            return True
        # Fallback for raw dict (from deserialized IR)
        if isinstance(node, dict):
            # Try to interpret as generic dict with kind
            kind = node.get("kind")
            if kind == "Constant":
                return node.get("value")
            if kind == "Name":
                nid = node.get("id", "")
                return ctx.get(nid, self._state.variables.get(nid, 0))
            # For dict call etc., attempt to eval via helper
            return 0
        return 0

    def _eval_call(self, node: IRCall, ctx: dict[str, Any], bar: Bar, view: BarView) -> Any:
        func = node.func
        args = tuple(self._eval_expr(a, ctx, bar, view) for a in node.args)
        # Parameter resolution: input(default, label)
        if func == "input":
            if not args:
                return 0
            default = args[0]
            label = args[1] if len(args) > 1 else None
            if label is not None and str(label) in self._params:
                return float(self._params[str(label)])
            # Synthetic param_1 etc. already in self._params
            # Fallback: try string of default
            key = str(label) if label is not None else str(default)
            if key in self._params:
                return float(self._params[key])
            return float(default) if isinstance(default, (int, float)) else 0
        # Indicator helpers — generic, not strategy-specific
        if func == "RSI":
            period = int(args[0]) if args else 14
            return self._calc_rsi(period)
        if func == "ATR":
            period = int(args[0]) if args else 14
            return self._calc_atr(period, bar)
        if func == "SMA":
            period = int(args[0]) if args else 14
            # second arg series ignored for now (always close)
            return self._calc_sma(period)
        if func == "EMA":
            period = int(args[0]) if args else 14
            return self._calc_ema(period)
        if func == "range":
            period = int(args[0]) if args else 14
            return self._calc_range(period, bar)
        # Actions — generic, produce pending signals
        if func == "buy":
            self._state.pending_kind = SignalKind.BUY
            return None
        if func == "sell":
            self._state.pending_kind = SignalKind.SELL
            return None
        if func == "close_position":
            if not view.state.flat:
                self._state.pending_kind = SignalKind.BUY if view.state.side == "SHORT" else SignalKind.SELL
            return None
        if func == "stop_loss":
            if args:
                self._state.pending_sl = float(args[0])
            return None
        if func == "take_profit":
            if args:
                self._state.pending_tp = float(args[0])
            return None
        if func in ("time_exit", "exit_time"):
            if args:
                self._state.pending_time_exit = str(args[0])
            return None
        if func == "strategy":
            return None
        # Unknown function — for determinism return 0, but could raise
        return 0

    # ── Indicator helpers (reuse logic from compiler) ──

    def _calc_rsi(self, period: int) -> float:
        closes = self._state.closes
        if len(closes) < period + 1:
            return 50.0
        vals = list(closes)[-period - 1 :]
        gains = sum(max(vals[i] - vals[i - 1], 0) for i in range(1, len(vals)))
        losses = sum(max(vals[i - 1] - vals[i], 0) for i in range(1, len(vals)))
        if losses == 0:
            return 100.0
        rs = gains / losses if losses else 0
        return 100 - (100 / (1 + rs))

    def _calc_atr(self, period: int, bar: Bar) -> float:
        highs = self._state.highs
        lows = self._state.lows
        if len(highs) < period:
            return bar.high - bar.low
        trs = [highs[i] - lows[i] for i in range(-period, 0)]
        return sum(trs) / period if trs else (bar.high - bar.low)

    def _calc_sma(self, period: int) -> float:
        closes = self._state.closes
        if len(closes) < period:
            return closes[-1] if closes else 0
        return sum(list(closes)[-period:]) / period

    def _calc_ema(self, period: int) -> float:
        closes = self._state.closes
        if len(closes) < period:
            return closes[-1] if closes else 0
        vals = list(closes)[-period:]
        k = 2 / (period + 1)
        ema = vals[0]
        for v in vals[1:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _calc_range(self, period: int, bar: Bar) -> float:
        highs = self._state.highs
        lows = self._state.lows
        if len(highs) < period or len(lows) < period:
            return bar.high - bar.low
        return max(list(highs)[-period:]) - min(list(lows)[-period:])

    def _eval_binop(self, op: str, left: Any, right: Any) -> Any:
        try:
            if op == "Add":
                return left + right
            if op == "Sub":
                return left - right
            if op == "Mult":
                return left * right
            if op == "Div":
                return left / right if right != 0 else 0
            if op == "Mod":
                return left % right if right != 0 else 0
            if op == "Pow":
                return left**right
        except Exception:
            return 0
        return 0

    def _eval_compare(self, op: str, left: Any, right: Any) -> bool:
        try:
            if op == "Eq":
                return left == right
            if op == "NotEq":
                return left != right
            if op == "Lt":
                return left < right
            if op == "LtE":
                return left <= right
            if op == "Gt":
                return left > right
            if op == "GtE":
                return left >= right
        except Exception:
            return False
        return False


def vm_from_ir(ir: StrategyIR, params: StrategyParameters | dict[str, float] | None = None) -> StrategyLogic:
    """Create a StrategyLogic-compatible VM instance from IR."""
    vm = StrategyVM(ir, params)
    # StrategyLogic protocol expects warmup/on_bar
    return vm  # type: ignore[return-value]
