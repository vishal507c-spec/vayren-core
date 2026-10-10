"""Strategy Assistant — safe AI-driven strategy analysis, code generation, diagnostics."""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from typing import Any

from strategy.language.compiler import StrategyLanguageError, compile_strategy


@dataclass(frozen=True)
class CodeDiagnostics:
    is_valid: bool
    syntax_error: str | None
    security_violations: list[str]
    detected_indicators: list[str]
    detected_timeframes: list[str]
    parameters: list[dict[str, Any]]
    warnings: list[str]


def analyze_strategy_code(code: str) -> CodeDiagnostics:
    """Analyze strategy source code for syntax, safety violations, parameters, and pitfalls."""
    security_violations: list[str] = []
    warnings: list[str] = []
    syntax_error: str | None = None
    detected_indicators: list[str] = []
    detected_timeframes: list[str] = []
    parameters: list[dict[str, Any]] = []

    # 1. AST Validation
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return CodeDiagnostics(
            is_valid=False,
            syntax_error=f"Line {e.lineno}, col {e.offset}: {e.msg}",
            security_violations=[],
            detected_indicators=[],
            detected_timeframes=[],
            parameters=[],
            warnings=["Fix syntax error before running or backtesting."],
        )

    # 2. Safety / AST scan
    blocked_imports = {
        "os",
        "sys",
        "subprocess",
        "socket",
        "pathlib",
        "shutil",
        "importlib",
        "ctypes",
        "requests",
        "urllib",
    }
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = (
                [a.name for a in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""]
            )
            for name in names:
                root = name.split(".")[0]
                if root in blocked_imports:
                    security_violations.append(
                        f"Forbidden import '{root}' is not permitted in sandbox environment."
                    )
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in ("eval", "exec", "compile", "open"):
                security_violations.append(
                    f"Forbidden call to '{node.func.id}()' violates sandbox policy."
                )

    # 3. Detect Indicators and patterns
    code_lower = code.lower()
    indicator_keywords = {
        "sma": "Simple Moving Average (SMA)",
        "ema": "Exponential Moving Average (EMA)",
        "rsi": "Relative Strength Index (RSI)",
        "macd": "MACD",
        "bollinger": "Bollinger Bands",
        "atr": "Average True Range (ATR)",
        "vwap": "Volume Weighted Average Price (VWAP)",
        "supertrend": "Supertrend",
    }
    for kw, label in indicator_keywords.items():
        if kw in code_lower:
            detected_indicators.append(label)

    # Timeframe detection
    for tf in ["1m", "3m", "5m", "15m", "30m", "1h", "1d"]:
        if f'"{tf}"' in code_lower or f"'{tf}'" in code_lower:
            detected_timeframes.append(tf)

    # Check for lookahead bias or future-leak pitfalls
    if "shift(-" in code_lower or "iloc[i+1" in code_lower:
        warnings.append("Potential lookahead bias detected: negative shift or future index.")

    # 4. Compilation attempt to verify logic & extract param_specs
    try:
        compiled = compile_strategy(code)
        for spec in compiled.param_specs:
            parameters.append(
                {
                    "key": spec.key,
                    "label": spec.label,
                    "default": spec.default,
                    "min": getattr(spec, "minimum", None),
                    "max": getattr(spec, "maximum", None),
                }
            )
    except StrategyLanguageError as exc:
        for err in exc.errors:
            if "import" in err or "allowed" in err:
                security_violations.append(err)
            else:
                warnings.append(err)
    except Exception as exc:
        warnings.append(f"Compilation warning: {exc}")

    is_valid = len(security_violations) == 0 and syntax_error is None
    return CodeDiagnostics(
        is_valid=is_valid,
        syntax_error=syntax_error,
        security_violations=security_violations,
        detected_indicators=detected_indicators,
        detected_timeframes=detected_timeframes,
        parameters=parameters,
        warnings=warnings,
    )


def generate_strategy_template(
    name: str,
    indicator: str = "SMA",
    timeframe: str = "15m",
    direction: str = "LONG",
) -> str:
    """Generate a clean, professional PythonStrategy starter template."""
    clean_name = re.sub(r"[^a-zA-Z0-9_]", "", name) or "CustomStrategy"
    return f'''"""Strategy: {name}
Indicator: {indicator}
Timeframe: {timeframe}
Direction: {direction}
"""

from strategy.strategies.base import PythonStrategy
from strategy.models.parameters import ParameterSpec


class {clean_name}(PythonStrategy):
    """{name} implementation built with Vayren Core."""

    @classmethod
    def param_specs(cls):
        return (
            ParameterSpec(
                key="fast_period",
                label="Fast Period",
                default=10.0,
                minimum=2.0,
                maximum=100.0,
            ),
            ParameterSpec(
                key="slow_period",
                label="Slow Period",
                default=30.0,
                minimum=5.0,
                maximum=300.0,
            ),
        )

    def on_bar_logic(self, view):
        # Access current bar: view.bar.open, view.bar.high, view.bar.low, view.bar.close
        bar = view.bar
        if bar.close > bar.open:
            self.buy()
        elif bar.close < bar.open:
            self.sell()
'''


def format_ai_prompt(prompt: str, existing_code: str = "") -> dict[str, Any]:
    """Format user prompt into structured AI instruction with context and safety rules."""
    context = ""
    if existing_code.strip():
        context = f"\n\nExisting Strategy Code:\n```python\n{existing_code}\n```"

    system_instruction = (
        "You are the Vayren Core Quantitative Strategy Specialist. "
        "Generate or refine Python trading strategies conforming to PythonStrategy. "
        "Do NOT import unauthorized libraries like os, sys, subprocess, or network clients. "
        "Ensure no lookahead bias and include parameter specifications."
    )
    return {
        "system": system_instruction,
        "user_prompt": f"{prompt}{context}",
    }
