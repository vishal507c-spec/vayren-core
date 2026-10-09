"""Provider adapters — provider master rows to canonical tuples (Phase 3).

The ONLY place provider-specific identity is interpreted. Each adapter
takes plain data (dicts/strings — never an SDK object) and returns a
normalized ``(provider_instrument_id, exchange, segment, symbol)`` tuple
ready for canonical matching, or raises :class:`ProviderParseError` with
an explicit reason. No fuzzy matching, no guessing, no SDK imports.

FYERS equity: provider symbol ``NSE:SBIN-EQ`` (series suffix mandatory —
a bare ``NSE:SBIN`` is AMBIGUOUS in FYERS-land: the series is unknown).
Zerodha (kite master rows): ``tradingsymbol`` + ``exchange`` + ``segment``
+ ``instrument_token``; kite segment ``NSE`` means equity, anything else is
out of Phase 3 scope.
"""

from __future__ import annotations

import re
from typing import Any

from strategy.models.instrument import EXCHANGE_NSE, SEGMENT_EQUITY

#: FYERS equity series suffixes that denote the EQUITY segment. A FYERS
#: symbol without a known suffix cannot be placed in a segment honestly.
FYERS_EQUITY_SERIES = ("EQ", "BE")

#: Kite segment codes that denote the EQUITY segment (Phase 3 scope).
ZERODHA_EQUITY_SEGMENTS = ("NSE",)

_FYERS_SYMBOL_RE = re.compile(r"^([A-Z0-9]+):([A-Z0-9._-]+?)-([A-Z0-9]+)$")

#: Providers with a master adapter in Phase 3 scope.
SUPPORTED_PROVIDER_NAMES = ("FYERS", "ZERODHA")


class ProviderParseError(ValueError):
    """A provider master row that cannot be normalized (reason attached)."""


def parse_fyers_symbol(raw: object) -> tuple[str, str, str, str]:
    """Normalize one FYERS provider symbol.

    Returns (provider_instrument_id, exchange, segment, symbol) where the
    provider id IS the FYERS symbol (``NSE:SBIN-EQ``) — that string is what
    FYERS market/order APIs address. Raises ProviderParseError when the
    series is missing/unknown (AMBIGUOUS) or the exchange is not NSE.
    """
    if not isinstance(raw, str):
        raise ProviderParseError(f"invalid FYERS symbol: {raw!r}")
    cleaned = raw.strip().upper()
    match = _FYERS_SYMBOL_RE.match(cleaned)
    if match is None:
        raise ProviderParseError(
            f"ambiguous FYERS symbol (series unknown, cannot place segment): {raw!r}"
        )
    exchange, symbol, series = match.groups()
    if exchange != EXCHANGE_NSE:
        raise ProviderParseError(f"unsupported FYERS exchange: {exchange!r}")
    if series not in FYERS_EQUITY_SERIES:
        raise ProviderParseError(f"unsupported FYERS series for NSE equity: {series!r} in {raw!r}")
    return (cleaned, exchange, SEGMENT_EQUITY, symbol)


def parse_zerodha_record(row: object) -> tuple[str, str, str, str]:
    """Normalize one kite-style master row (plain dict, no SDK).

    Expects tradingsymbol + exchange + segment + instrument_token. The
    provider id is the token as text. Raises ProviderParseError for
    missing fields, non-NSE exchanges, and non-equity segments.
    """
    if not isinstance(row, dict):
        raise ProviderParseError(f"invalid Zerodha master row: {row!r}")
    symbol = str(row.get("tradingsymbol", "") or "").strip().upper()
    exchange = str(row.get("exchange", "") or "").strip().upper()
    segment = str(row.get("segment", "") or "").strip().upper()
    token = row.get("instrument_token", "")
    token_text = str(token).strip() if isinstance(token, (int, str)) else ""
    if not symbol or not token_text:
        raise ProviderParseError(f"zerodha row lacks symbol/token: {row!r}")
    if exchange != EXCHANGE_NSE:
        raise ProviderParseError(f"unsupported Zerodha exchange: {exchange!r}")
    if segment not in ZERODHA_EQUITY_SEGMENTS:
        raise ProviderParseError(
            f"unsupported Zerodha segment for NSE equity: {segment!r} in {symbol!r}"
        )
    return (token_text, exchange, SEGMENT_EQUITY, symbol)


def parse_master_row(provider: str, row: Any) -> tuple[str, str, str, str]:
    """Dispatch one master row to its provider adapter (name → parser).

    Unknown provider names fail explicitly — there is no default parser
    and no guessing. This dispatch is the only provider-name branch in
    the mapping layer; the registry itself never names a provider.
    """
    name = str(provider or "").strip().upper()
    if name == "FYERS":
        if isinstance(row, dict):
            return parse_fyers_symbol(row.get("symbol", ""))
        return parse_fyers_symbol(row)
    if name == "ZERODHA":
        return parse_zerodha_record(row)
    raise ProviderParseError(f"no master adapter for provider {name!r}")


__all__ = [
    "ProviderParseError",
    "parse_fyers_symbol",
    "parse_zerodha_record",
    "parse_master_row",
    "FYERS_EQUITY_SERIES",
    "ZERODHA_EQUITY_SEGMENTS",
    "SUPPORTED_PROVIDER_NAMES",
]
