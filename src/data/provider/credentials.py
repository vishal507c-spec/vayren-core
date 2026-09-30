"""Provider credential schema — the provider layer's configuration contract.

The engine never sees this. A provider advertises the credentials it needs
as a tuple of :class:`CredentialField`; the in-app credentials manager and
the configuration dialog render/save/clear exactly those fields, so a future
broker defines its own credential shape without touching the engine or the
UI flow.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CredentialField:
    """One configurable credential of a provider.

    ``key`` is the storage key (and the dict key used by the manager);
    ``label`` is the UI caption; ``secret`` marks values that must always be
    masked in the UI and never logged; ``required`` enables empty-value
    validation before saving.
    """

    key: str
    label: str
    secret: bool = False
    required: bool = False
    help: str = ""


class ProviderConfigError(ValueError):
    """Raised when provider credential configuration cannot complete.

    Messages never contain credential values.
    """
