"""Secret redaction for every artifact the migration system emits.

Manifests, fixtures, logs, reports and telemetry must never carry broker
credentials, tokens or passwords. When in doubt, redact.
"""

from __future__ import annotations

import re

from .config import SECRET_PATTERNS, SECRET_REDACTION

_ASSIGN_RE = re.compile(
    r"(?i)(['\"]?(?:" + "|".join(SECRET_PATTERNS) + r")['\"]?\s*[:=]\s*)(['\"]?)([^\s,'\"}\]]+)(\2)"
)


def redact_text(text: str) -> str:
    """Replace ``key = value`` secret assignments with a fixed token."""
    return _ASSIGN_RE.sub(lambda m: m.group(1) + SECRET_REDACTION, text)


def redact_mapping(data: dict) -> dict:
    """Return a copy of a flat mapping with secret-looking keys redacted."""
    cleaned: dict = {}
    for key, value in data.items():
        lowered = str(key).lower()
        if any(token in lowered for token in SECRET_PATTERNS):
            cleaned[key] = SECRET_REDACTION
        else:
            cleaned[key] = value
    return cleaned


def contains_secret_literal(text: str) -> str | None:
    """Return the offending pattern name if a secret assignment is present."""
    lowered = text.lower()
    for token in SECRET_PATTERNS:
        if token in lowered:
            return token
    return None


__all__ = ["redact_text", "redact_mapping", "contains_secret_literal"]
