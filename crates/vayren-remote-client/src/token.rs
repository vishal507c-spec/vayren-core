//! Remote-token resolution — platform secure storage, never hardcoded.
//!
//! Precedence: `VAYREN_REMOTE_TOKEN` env var first (operators, CI, and the
//! Android/Windows launchers that inject it), then the OS credential store
//! (Windows Credential Manager / macOS Keychain / Linux Secret Service via
//! the `keyring` crate). Broker credentials are a different universe — this
//! module never reads them and would not accept them: values shorter than
//! [`MIN_TOKEN_LENGTH`] fail closed, exactly like the gateway.
//!
//! The token VALUE is never logged, never included in errors, and never
//! serialized. Errors name the SOURCE only.

use std::fmt;

/// Minimum accepted token length (mirrors the gateway's fail-closed floor).
pub const MIN_TOKEN_LENGTH: usize = 16;

/// Environment variable carrying the remote bearer token.
pub const TOKEN_ENV_VAR: &str = "VAYREN_REMOTE_TOKEN";

/// OS credential-store slot for the remote token.
pub const KEYRING_SERVICE: &str = "vayren-remote";
/// OS credential-store account for the remote token.
pub const KEYRING_ACCOUNT: &str = "remote-token";

/// Token resolution failure — the SOURCE is named, never the value.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum TokenError {
    /// No token in env or in the OS store.
    Missing,
    /// A token was found but is shorter than [`MIN_TOKEN_LENGTH`].
    TooShort { source: String },
    /// The OS store could not be read.
    Store { source: String, detail: String },
}

impl fmt::Display for TokenError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Missing => write!(
                f,
                "no remote token: set {TOKEN_ENV_VAR} or store one under {KEYRING_SERVICE}/{KEYRING_ACCOUNT}"
            ),
            Self::TooShort { source } => {
                write!(f, "remote token from {source} is shorter than {MIN_TOKEN_LENGTH} chars")
            }
            Self::Store { source, detail } => {
                write!(f, "remote token store {source} unreadable: {detail}")
            }
        }
    }
}

impl std::error::Error for TokenError {}

/// One token source (injectable — tests use fakes, never the OS store).
pub trait TokenStore {
    /// Load the bearer token, or the reason it cannot be provided.
    fn load_remote_token(&self) -> Result<String, TokenError>;
}

/// Tokens from the process environment (launchers, services, CI).
pub struct EnvTokenStore {
    var: String,
}

impl EnvTokenStore {
    pub fn standard() -> Self {
        Self {
            var: TOKEN_ENV_VAR.to_string(),
        }
    }
}

impl TokenStore for EnvTokenStore {
    fn load_remote_token(&self) -> Result<String, TokenError> {
        match std::env::var(&self.var) {
            Ok(value) if value.trim().len() >= MIN_TOKEN_LENGTH => Ok(value.trim().to_string()),
            Ok(_) => Err(TokenError::TooShort {
                source: format!("env:{TOKEN_ENV_VAR}"),
            }),
            Err(_) => Err(TokenError::Missing),
        }
    }
}

/// Tokens from the platform credential store (the EXE/APK path).
pub struct KeyringTokenStore {
    service: String,
    account: String,
}

impl KeyringTokenStore {
    pub fn standard() -> Self {
        Self {
            service: KEYRING_SERVICE.to_string(),
            account: KEYRING_ACCOUNT.to_string(),
        }
    }
}

impl TokenStore for KeyringTokenStore {
    fn load_remote_token(&self) -> Result<String, TokenError> {
        let source = format!("keyring:{}/{}", self.service, self.account);
        let entry = match keyring::Entry::new(&self.service, &self.account) {
            Ok(entry) => entry,
            // No usable credential backend on this host (headless machines,
            // minimal containers): nothing retrievable is configured here.
            Err(keyring::Error::NoDefaultStore) => return Err(TokenError::Missing),
            Err(err) => {
                return Err(TokenError::Store {
                    source: source.clone(),
                    detail: err.to_string(),
                });
            }
        };
        match entry.get_password() {
            Ok(value) if value.trim().len() >= MIN_TOKEN_LENGTH => Ok(value.trim().to_string()),
            Ok(_) => Err(TokenError::TooShort { source }),
            Err(keyring::Error::NoEntry) | Err(keyring::Error::NoStorageAccess(_)) => {
                // No entry, or no reachable store at all (headless hosts,
                // locked stores): in both cases nothing retrievable is
                // configured here — the actionable answer is identical.
                Err(TokenError::Missing)
            }
            Err(err) => Err(TokenError::Store {
                source,
                detail: err.to_string(),
            }),
        }
    }
}

/// Resolve the remote token: env first, then the OS store.
///
/// Returns the token VALUE (handle it like a secret: send it only inside
/// the `hello` frame, never log or persist it elsewhere).
pub fn resolve_remote_token() -> Result<String, TokenError> {
    match EnvTokenStore::standard().load_remote_token() {
        Ok(token) => Ok(token),
        Err(TokenError::Missing) => KeyringTokenStore::standard().load_remote_token(),
        Err(other) => Err(other),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Mutex;

    static ENV_GUARD: Mutex<()> = Mutex::new(());

    struct FakeStore(Result<String, TokenError>);

    impl TokenStore for FakeStore {
        fn load_remote_token(&self) -> Result<String, TokenError> {
            match &self.0 {
                Ok(token) => Ok(token.clone()),
                Err(err) => Err(err.clone()),
            }
        }
    }

    #[test]
    fn fake_store_roundtrips() {
        let _guard = ENV_GUARD.lock().unwrap();
        assert!(FakeStore(Ok("a-very-long-token-value".to_string()))
            .load_remote_token()
            .is_ok());
        assert_eq!(
            FakeStore(Err(TokenError::Missing)).load_remote_token(),
            Err(TokenError::Missing)
        );
    }

    #[test]
    fn env_store_accepts_long_rejects_short_and_missing() {
        let _guard = ENV_GUARD.lock().unwrap();
        let store = EnvTokenStore::standard();
        std::env::set_var(TOKEN_ENV_VAR, "short");
        assert!(matches!(
            store.load_remote_token(),
            Err(TokenError::TooShort { .. })
        ));
        std::env::set_var(TOKEN_ENV_VAR, "a-sufficiently-long-token-0123");
        assert_eq!(
            store.load_remote_token().unwrap(),
            "a-sufficiently-long-token-0123"
        );
        std::env::remove_var(TOKEN_ENV_VAR);
        assert_eq!(store.load_remote_token(), Err(TokenError::Missing));
    }

    #[test]
    fn errors_never_carry_the_value() {
        let _guard = ENV_GUARD.lock().unwrap();
        std::env::set_var(TOKEN_ENV_VAR, "tiny");
        let text = EnvTokenStore::standard()
            .load_remote_token()
            .unwrap_err()
            .to_string();
        assert!(!text.contains("tiny"));
        std::env::remove_var(TOKEN_ENV_VAR);
    }
}
