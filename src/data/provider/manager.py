"""ProviderCredentialsManager — provider-agnostic in-app credential config.

Layering (per the provider layer):

    Provider Factory → selected Provider → Provider Configuration

The manager only talks to the selected provider instance through its
duck-typed credential surface (``credential_fields``, ``display_name``,
``build_credentials``, ``reload_credentials``) and the engine's
``available()`` contract method. The engine, the contract, the factory and
the download flow never appear here.

Persistence goes to a :class:`~data.provider.credentials_store.CredentialStore`
(OS credential mechanism, file fallback). Credential values are never
logged and never included in messages; validation errors only name the
missing field label.
"""

from __future__ import annotations

from typing import Any

from data.provider.credentials import CredentialField, ProviderConfigError
from data.provider.credentials_store import (
    CredentialStore,
    default_store,
    provider_service,
)
from data.settings import DownloadSettings


class ProviderCredentialsManager:
    """Configuration service for one live provider instance."""

    def __init__(
        self,
        settings: DownloadSettings,
        provider: Any,
        store: CredentialStore | None = None,
    ) -> None:
        self._settings = settings
        self._provider = provider
        self._store = store if store is not None else default_store(settings.data_dir)
        self._service = provider_service(settings.provider)

    # ── provider schema ──────────────────────────────────────────────────────

    @property
    def display_name(self) -> str:
        return getattr(self._provider, "display_name", type(self._provider).__name__)

    @property
    def fields(self) -> tuple[CredentialField, ...]:
        fields = getattr(self._provider, "credential_fields", None)
        if not fields:
            raise ProviderConfigError(f"{self.display_name} has no configurable credentials")
        return tuple(fields)

    # ── values ───────────────────────────────────────────────────────────────

    def load_values(self) -> dict[str, str]:
        """Stored values for the provider's fields (empty strings if none)."""
        stored = self._store.load(self._service) or {}
        return {field.key: stored.get(field.key, "") for field in self.fields}

    def has_stored(self) -> bool:
        return any(self.load_values().values())

    def validate(self, values: dict[str, str]) -> str | None:
        """First missing required field as a label-only message, else None."""
        for field in self.fields:
            if field.required and not values.get(field.key, "").strip():
                return f"{field.label} is required."
        return None

    # ── operations ───────────────────────────────────────────────────────────

    def apply(self, values: dict[str, str]) -> tuple[bool, str]:
        """Load the given values into the live provider (no persistence).

        Returns the provider's availability ``(ready, reason)``.
        """
        credentials = self._provider.build_credentials(values)
        self._provider.reload_credentials(credentials)
        return self._provider.available()

    def test_connection(self, values: dict[str, str]) -> tuple[bool, str]:
        return self.apply(values)

    def save(self, values: dict[str, str]) -> tuple[bool, str]:
        """Validate, persist securely, reload the provider, report status."""
        error = self.validate(values)
        if error is not None:
            raise ProviderConfigError(error)
        clean = {field.key: values.get(field.key, "").strip() for field in self.fields}
        self._store.save(self._service, clean)
        return self.apply(clean)

    def clear(self) -> tuple[bool, str]:
        """Delete the stored credentials and reload (store → env fallback)."""
        self._store.delete(self._service)
        return self.reload()

    def reload(self) -> tuple[bool, str]:
        """Rebuild the provider from persisted state; returns availability."""
        self._provider.reload_credentials()
        return self._provider.available()
