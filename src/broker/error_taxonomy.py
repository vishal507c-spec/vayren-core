"""Broker error taxonomy — classify 400/429/timeouts/WS failures correctly.

Every HTTP 400 is NOT an expired token. Rate limits respect Retry-After with
bounded exponential backoff + jitter. Timeouts are uncertain (never assumed
failed for orders — reconcile first). Secrets are redacted from all reasons.
"""

from __future__ import annotations

import random
import re
from typing import Any

_TOKEN_LIKE = re.compile(
    r"(?i)(access_token|refresh_token|auth_code|api_key|apikey|secret|totp|password|"
    r"passwd|private_key|seed|otp|session_token)\s*[:=]\s*([^\s,;&\"']+)"
)
_BEARER_LIKE = re.compile(r"(?i)bearer\s+([A-Za-z0-9\-._~+/=]+)")
_URL_TOKEN = re.compile(r"(?i)([?&](?:token|code|auth_code|access_token)=)([^&\s]+)")


def redact(text: str) -> str:
    """Redact secret-looking values from free-text errors/URLs/diagnostics."""
    if not text:
        return ""
    out = _TOKEN_LIKE.sub(lambda m: f"{m.group(1)}=***", str(text))
    out = _BEARER_LIKE.sub("Bearer ***", out)
    out = _URL_TOKEN.sub(lambda m: f"{m.group(1)}***", out)
    # Raw FYERS-style "APPID:TOKEN" authorization values.
    out = re.sub(r"(?i)(authorization\s*[:=]\s*[A-Z0-9\-]+:)[^\s\"']+", r"\1***", out)
    return out


def classify_http_error(status: int, message: str = "") -> tuple[str, str]:
    """Classify an HTTP failure into (kind, safe_reason).

    Kinds: AUTH_EXPIRED, AUTH_INVALID, BAD_REQUEST, RATE_LIMITED, NETWORK,
    VENUE, UNKNOWN. A 400 is only AUTH when the body says token/session; a
    malformed-parameter 400 stays BAD_REQUEST.
    """
    msg = redact(str(message or ""))
    lowered = msg.lower()
    if status == 429:
        return "RATE_LIMITED", f"rate limited by venue: {msg or 'HTTP 429'}"
    if status in (401, 403):
        if any(k in lowered for k in ("expired", "invalid token", "unauthorized", "session")):
            return "AUTH_EXPIRED", f"session expired or invalid: {msg or f'HTTP {status}'}"
        return "AUTH_INVALID", f"venue rejected authorization: {msg or f'HTTP {status}'}"
    if status == 400:
        if any(
            k in lowered
            for k in (
                "expired",
                "invalid token",
                "token is invalid",
                "session",
                "unauthorized",
                "auth",
                "token",
            )
        ):
            return "AUTH_EXPIRED", f"session expired or invalid: {msg or 'HTTP 400'}"
        return "BAD_REQUEST", f"venue rejected the request: {msg or 'HTTP 400'}"
    if status in (408, 504) or "timeout" in lowered or "timed out" in lowered:
        return "TIMEOUT", f"venue timed out: {msg or f'HTTP {status}'}"
    if status >= 500 or status in (308,):
        return "VENUE", f"venue error: {msg or f'HTTP {status}'}"
    if "unreachable" in lowered or "connection refused" in lowered or "oserror" in lowered:
        return "NETWORK", f"venue unreachable: {msg or 'network error'}"
    return "UNKNOWN", redact(msg or f"HTTP {status}")


def parse_retry_after(
    headers: Any,
    default_s: float = 5.0,
    min_s: float = 5.0,
    max_s: float = 300.0,
    attempt: int = 0,
    add_jitter: bool = False,
) -> float:
    """Parse Retry-After (seconds) with a bounded positive floor; never zero or negative.

    Guarantees a safe positive floor (min_s, default 5.0s) so 'Retry-After: 0' or
    missing headers cannot trigger immediate retries. Applies bounded exponential
    backoff across attempts and optional jitter.
    """
    delay = None
    try:
        if isinstance(headers, dict):
            raw = headers.get("Retry-After", headers.get("retry-after", ""))
            if str(raw).strip():
                delay = float(str(raw).strip())
    except (TypeError, ValueError):
        delay = None

    floor = max(1.0, float(min_s))
    if delay is None or delay < floor:
        base = max(floor, float(default_s))
    else:
        base = min(delay, float(max_s))

    if attempt > 0:
        base = min(base * (2.0 ** min(attempt, 6)), float(max_s))

    if add_jitter:
        jitter = random.uniform(0.0, min(base * 0.25, 5.0))
        base += jitter

    return round(max(floor, min(base, float(max_s))), 2)


def backoff_with_jitter(attempt: int, base_s: float = 1.0, cap_s: float = 60.0) -> float:
    """Bounded exponential backoff with jitter (attempt 0-based)."""
    attempt = max(0, int(attempt))
    delay = min(float(base_s) * (2.0**attempt), float(cap_s))
    jitter = random.uniform(0.0, min(delay * 0.25, 5.0))
    return delay + jitter


def classify_exception(exc: BaseException) -> tuple[str, str]:
    """Classify a transport exception into (kind, safe_reason).

    Inspects structured HTTP status codes and exception types first, then falls back
    to robust keyword analysis where token expiry always takes precedence over
    incidental network/unreachable framing.
    """
    import socket
    import urllib.error

    # 1. Structured HTTP status code if present on exception object
    http_code = (
        getattr(exc, "http_status", None)
        or getattr(exc, "code", None)
        or getattr(exc, "status_code", None)
    )
    if isinstance(http_code, int) and 100 <= http_code <= 599:
        return classify_http_error(http_code, str(exc))

    # 2. Structured Python exception types
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return "TIMEOUT", f"venue timed out: {redact(str(exc))}"
    if isinstance(exc, ConnectionRefusedError):
        return "NETWORK", "venue unreachable: connection refused"
    if isinstance(exc, urllib.error.URLError):
        reason = getattr(exc, "reason", exc)
        if isinstance(reason, (socket.timeout, TimeoutError)):
            return "TIMEOUT", f"venue timed out: {redact(str(reason))}"
        return "NETWORK", f"venue unreachable: {redact(str(reason))}"

    # 3. String content analysis with strict priority
    text = redact(str(exc) or type(exc).__name__)
    lowered = text.lower()
    if any(k in lowered for k in ("429", "rate limit", "too many requests")):
        return "RATE_LIMITED", f"rate limited by venue: {text}"
    if any(
        k in lowered
        for k in (
            "token is invalid",
            "invalid token",
            "token expired",
            "expired",
            "unauthorized",
            "401",
            "session expired",
        )
    ):
        return "AUTH_EXPIRED", f"session expired or invalid: {text}"
    if any(
        k in lowered
        for k in (
            "mismatch",
            "invalid pin",
            "invalid totp",
            "bad credentials",
            "rejected authorization",
        )
    ):
        return "AUTH_INVALID", f"venue rejected credentials: {text}"
    if "400" in lowered:
        return classify_http_error(400, text)
    if any(k in lowered for k in ("timeout", "timed out")):
        return "TIMEOUT", f"venue timed out: {text}"
    if any(
        k in lowered for k in ("unreachable", "connection refused", "oserror", "network", "socket")
    ):
        return "NETWORK", f"venue unreachable: {text}"
    return "UNKNOWN", text


__all__ = [
    "backoff_with_jitter",
    "classify_exception",
    "classify_http_error",
    "parse_retry_after",
    "redact",
]
