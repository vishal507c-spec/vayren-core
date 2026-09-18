"""FYERS adapter — the venue's broker knowledge in one package.

``FyersProvider`` (adapter.py) implements ``data.provider.contract.Provider``
(fail-closed history in the authentication phase);
``FyersAuthFlow``/``FyersSessionStore`` (live_auth.py) own the official
FYERS API v3 login → token → verified-profile flow;
``FyersCredentials`` (credentials.py) owns layered credential resolution;
``FyersSessionAdapter`` (session_adapter.py) is the authenticated read-only
session (no order placement exists anywhere in this package).
"""

from data.provider.fyers.adapter import FyersProvider
from data.provider.fyers.auto_auth import FyersAutoAuthEngine
from data.provider.fyers.credentials import FyersCredentials, load_fyers_credentials
from data.provider.fyers.live_auth import (
    FyersAuthFlow,
    FyersSessionStore,
    fyers_interactive_login,
)
from data.provider.fyers.session_adapter import FyersSessionAdapter

__all__ = [
    "FyersAuthFlow",
    "FyersAutoAuthEngine",
    "FyersCredentials",
    "FyersProvider",
    "FyersSessionAdapter",
    "FyersSessionStore",
    "fyers_interactive_login",
    "load_fyers_credentials",
]
