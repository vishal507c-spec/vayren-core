"""Indicator helpers for native Python strategies — pure Python, no DSL."""

from __future__ import annotations

from collections import deque


def calc_rsi(closes: deque[float], period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    vals = list(closes)[-period - 1 :]
    gains = sum(max(vals[i] - vals[i - 1], 0) for i in range(1, len(vals)))
    losses = sum(max(vals[i - 1] - vals[i], 0) for i in range(1, len(vals)))
    if losses == 0:
        return 100.0
    rs = gains / losses if losses else 0
    return 100 - (100 / (1 + rs))


def calc_atr(
    highs: deque[float], lows: deque[float], _closes: deque[float], period: int = 14
) -> float:
    if len(highs) < period:
        return (highs[-1] - lows[-1]) if highs and lows else 0.0
    trs = [highs[i] - lows[i] for i in range(-period, 0)]
    return sum(trs) / period if trs else 0.0


def calc_sma(closes: deque[float], period: int = 14) -> float:
    if len(closes) < period:
        return closes[-1] if closes else 0.0
    return sum(list(closes)[-period:]) / period


def calc_ema(closes: deque[float], period: int = 14) -> float:
    if len(closes) < period:
        return closes[-1] if closes else 0.0
    vals = list(closes)[-period:]
    k = 2 / (period + 1)
    ema = vals[0]
    for v in vals[1:]:
        ema = v * k + ema * (1 - k)
    return ema


def calc_range(highs: deque[float], lows: deque[float], period: int = 14) -> float:
    if len(highs) < period or len(lows) < period:
        return (highs[-1] - lows[-1]) if highs and lows else 0.0
    return max(list(highs)[-period:]) - min(list(lows)[-period:])
