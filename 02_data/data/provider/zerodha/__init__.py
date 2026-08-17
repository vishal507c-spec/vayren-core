"""Zerodha adapter — the ONLY broker knowledge in the system.

``ZerodhaProvider`` (adapter.py) implements ``data.provider.contract.Provider``
with canonical symbols, canonical intervals and normalized error codes.
Everything Zerodha-specific (KiteConnect SDK, credentials, access tokens, Kite
interval ids, instrument tokens, API requests, authentication, errors, rate
limits) lives in this package and nowhere else.
"""

from data.provider.zerodha.adapter import ZerodhaProvider
from data.provider.zerodha.auth import AuthEngine, AuthError
from data.provider.zerodha.credentials import ZerodhaCredentials

__all__ = [
    "ZerodhaProvider",
    "AuthEngine",
    "AuthError",
    "ZerodhaCredentials",
]
