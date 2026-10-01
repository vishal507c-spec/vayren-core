"""Broker common infrastructure — credential stores, credential schemas, and drivers."""

from __future__ import annotations

from broker.common.credentials import BrokerConfigError, CredentialField, ProviderConfigError
from broker.common.credentials_store import (
    CredentialStore,
    FileCredentialStore,
    WindowsCredentialStore,
    default_store,
    provider_service,
)
from broker.common.manager import BrokerCredentialsManager, ProviderCredentialsManager
from broker.common.selenium_driver import (
    BrowserUnavailableError,
    ChromeSession,
    SeleniumAuthError,
    build_chrome,
    resolve_chromedriver,
)

__all__ = [
    "BrokerConfigError",
    "BrokerCredentialsManager",
    "BrowserUnavailableError",
    "ChromeSession",
    "CredentialField",
    "CredentialStore",
    "FileCredentialStore",
    "ProviderConfigError",
    "ProviderCredentialsManager",
    "SeleniumAuthError",
    "WindowsCredentialStore",
    "build_chrome",
    "default_store",
    "provider_service",
    "resolve_chromedriver",
]
