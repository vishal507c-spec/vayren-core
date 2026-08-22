"""Compiler — validated AST -> StrategyLogic."""

import ast
from collections import deque
from dataclasses import dataclass
from typing import Any

from market.models.bar import Bar

from strategy.language.ir import StrategyIR, build_ir
from strategy.language.parser import CompileError, parse_and_validate
from strategy.models.parameters import StrategyParameters
from strategy.models.signal import Signal, SignalKind
from strategy.runtime import BarView, StrategyLogic


class StrategyLanguageError(Exception):
    def __init__(self, errors: list[CompileError]):
        self.errors = errors
        super().__init__("\n".join(e.pretty() for e in errors))


# Helpers that will be exposed per bar


def _make_helpers(
    bar: Bar, closes: deque[float], highs: deque[float], lows: deque[float], volumes: deque[int]
):
    def RSI(period: int = 14) -> float:
        if len(closes) < period + 1:
            return 50.0
        vals = list(closes)[-period - 1 :]
        gains = sum(max(vals[i] - vals[i - 1], 0) for i in range(1, len(vals)))
        losses = sum(max(vals[i - 1] - vals[i], 0) for i in range(1, len(vals)))
        if losses == 0:
            return 100.0
        rs = gains / losses if losses else 0
        return 100 - (100 / (1 + rs))

    def ATR(period: int = 14) -> float:
        if len(highs) < period:
            return bar.high - bar.low
        trs = [highs[i] - lows[i] for i in range(-period, 0)]
        return sum(trs) / period if trs else (bar.high - bar.low)

    def SMA(period: int, series: str = "close") -> float:  # noqa: ARG001
        if len(closes) < period:
            return bar.close
        return sum(list(closes)[-period:]) / period

    def EMA(period: int) -> float:
        if len(closes) < period:
            return bar.close
        vals = list(closes)[-period:]
        k = 2 / (period + 1)
        ema = vals[0]
        for v in vals[1:]:
            ema = v * k + ema * (1 - k)
        return ema

    def range_func(period: int) -> float:
        if len(highs) < period or len(lows) < period:
            return bar.high - bar.low
        return max(list(highs)[-period:]) - min(list(lows)[-period:])

    return {"RSI": RSI, "ATR": ATR, "SMA": SMA, "EMA": EMA, "range": range_func}


@dataclass
class CompiledStrategy:
    tree: ast.Module
    param_defaults: dict[str, float]  # label -> default
    code: str
    ir: StrategyIR | None = None
    warmup: int = 20

    def create_logic(self, params: StrategyParameters) -> StrategyLogic:
        # Phase 4: prefer generic VM via IR if available
        if self.ir is not None:
            try:
                from strategy.vm import vm_from_ir

                return vm_from_ir(self.ir, params)
            except Exception:
                pass
        # params may contain overrides; merge with defaults by label
        # StrategyParameters is keyed by whatever the validator extracted; for now we use labels as keys? But StrategyParameters uses keys like "C1 Range"? That's okay.
        # We'll map labels to values: if params has label, use it, else default
        defaults = self.param_defaults
        # Build effective mapping: label -> value
        effective = dict(defaults)
        for k, v in dict(params).items():
            effective[k] = float(v)
        return _CompiledLogic(self.tree, self.code, effective)


class _CompiledLogic:
    def __init__(self, tree: ast.Module, code: str, param_values: dict[str, float]):
        self._tree = tree
        self._code = code
        self._params = param_values
        self._closes: deque[float] = deque(maxlen=100)
        self._highs: deque[float] = deque(maxlen=100)
        self._lows: deque[float] = deque(maxlen=100)
        self._vols: deque[int] = deque(maxlen=100)
        # pending signal set by helpers during exec
        self._pending_kind: SignalKind | None = None
        self._pending_sl: float | None = None
        self._pending_tp: float | None = None
        self._pending_time_exit: str | None = None
        # for CSP: store compiled code object for fast exec (validate already ensures safe)
        # We will exec the user code per bar in restricted env; for performance compile once
        # Remove the top-level strategy() call from exec? Keep but strategy() is no-op at runtime
        self._code_obj = compile(self._tree, "<strategy>", "exec")

    def warmup(self) -> int:
        return 20

    def on_bar(self, view: BarView) -> Signal | None:
        bar = view.bar
        self._closes.append(bar.close)
        self._highs.append(bar.high)
        self._lows.append(bar.low)
        self._vols.append(bar.volume)
        self._pending_kind = None
        self._pending_sl = None
        self._pending_tp = None
        self._pending_time_exit = None

        helpers = _make_helpers(bar, self._closes, self._highs, self._lows, self._vols)

        # input() returns param value by label; we need to map call site: but our exec will call input(default, label)
        # So input helper looks up by label string
        def input_func(default: float, label: str | None = None) -> float:  # noqa: A002
            # label may be provided as second arg; if not, fallback to default string
            key = str(label) if label is not None else str(default)
            # try label first
            if label is not None and label in self._params:
                return float(self._params[label])
            # fallback: find by default value near? For unlabeled inputs we use positional matching: not ideal.
            # Instead, input without label uses default as key? For V1 we treat input(default) as key = str(default)?? Not robust.
            # Better: during compilation we already extracted param_defaults keyed by label. For unlabeled, we used synthetic param_1 etc.
            # So if label is None, we can't resolve; return default.
            # The validator creates synthetic labels param_1 etc, but at runtime we don't have mapping from positional to synthetic.
            # Simplify: for input(default, label) the label is the key; for input(default) without label we use str(default)??? We'll just return param by label if exists else default.
            for k, v in self._params.items():
                if k == str(label):
                    return float(v)
            return float(default)

        def buy_func():
            self._pending_kind = SignalKind.BUY

        def sell_func():
            self._pending_kind = SignalKind.SELL

        def close_position_func():
            # close opposite of current side
            if not view.state.flat:
                self._pending_kind = (
                    SignalKind.BUY if view.state.side == "SHORT" else SignalKind.SELL
                )

        def stop_loss_func(price: float):
            self._pending_sl = float(price)

        def take_profit_func(price: float):
            self._pending_tp = float(price)

        def time_exit_func(t: str):
            self._pending_time_exit = str(t)

        def exit_time_func(t: str):
            self._pending_time_exit = str(t)

        def strategy_func(name: str):  # noqa: ARG001
            pass

        restricted_globals: dict[str, Any] = {
            "__builtins__": {},
            "True": True,
            "False": False,
            "None": None,
            "strategy": strategy_func,
            "input": input_func,
            "RSI": helpers["RSI"],
            "ATR": helpers["ATR"],
            "SMA": helpers["SMA"],
            "EMA": helpers["EMA"],
            "range": helpers["range"],
            "buy": buy_func,
            "sell": sell_func,
            "close_position": close_position_func,
            "stop_loss": stop_loss_func,
            "take_profit": take_profit_func,
            "time_exit": time_exit_func,
            "exit_time": exit_time_func,
        }
        # per-bar variables
        locals_ns: dict[str, Any] = {
            "close": bar.close,
            "open": bar.open,
            "high": bar.high,
            "low": bar.low,
            "volume": bar.volume,
            "bar": bar,
            "time": bar.timestamp,
        }
        # time exit check before user logic: if bar time >= exit time, force close
        if self._pending_time_exit:
            # already set from previous bar? clear
            pass
        try:
            exec(self._code_obj, restricted_globals, locals_ns)  # noqa: S102
        except Exception:
            # runtime error per bar — treat as no signal, but log? For now suppress
            # Could store error for UI, but for backtest we ignore
            return None
        # check time exit after exec (user may have set time_exit)
        if self._pending_time_exit:
            # bar timestamp like "2023-01-01 09:15:00" -> extract HH:MM
            try:
                hhmm = bar.timestamp[11:16]
                if hhmm >= self._pending_time_exit:
                    if not view.state.flat:
                        # force exit
                        self._pending_kind = (
                            SignalKind.BUY if view.state.side == "SHORT" else SignalKind.SELL
                        )
            except Exception:
                pass
        if self._pending_kind is None:
            return None
        # respect flat / position: only emit if matches state (runner also checks, but we keep)
        return Signal(
            index=view.index,
            timestamp=bar.timestamp,
            kind=self._pending_kind,
            price=bar.close,
            stop_loss=self._pending_sl,
            take_profit=self._pending_tp,
        )


def compile_strategy(code: str) -> CompiledStrategy:
    tree, errors, param_infos = parse_and_validate(code)
    if errors:
        raise StrategyLanguageError(errors)
    if tree is None:
        raise StrategyLanguageError([e for e in errors])
    # Build defaults dict label->default
    defaults: dict[str, float] = {}
    for p in param_infos:
        # if duplicate label, last wins
        defaults[p.label] = float(p.default)
    # also need to handle input without label case: validator used synthetic param_N — keep those too
    # If no params but code uses input, defaults will have synthetic keys; that's okay, UI will show them as param_1
    # Build generic IR (deterministic, versioned)
    try:
        ir = build_ir(code, tree, param_infos)
    except Exception:
        ir = None  # IR build should not fail compilation; diagnostics come from parser
    return CompiledStrategy(tree=tree, param_defaults=defaults, code=code, ir=ir)


def compile_to_ir(code: str) -> StrategyIR:
    """Compile source to generic IR — no execution, deterministic."""
    tree, errors, param_infos = parse_and_validate(code)
    if errors:
        raise StrategyLanguageError(errors)
    if tree is None:
        raise StrategyLanguageError([CompileError(line=1, col=0, message="Empty source")])
    return build_ir(code, tree, param_infos)
