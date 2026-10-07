//! Schema-v1 gateway client: transport, token resolution, reconnect.
//!
//! ```rust,no_run
//! use std::time::Duration;
//! use vayren_remote_client::{RemoteClient, resolve_remote_token};
//!
//! let token = resolve_remote_token().expect("set VAYREN_REMOTE_TOKEN");
//! // Read-only probe: hello → welcome → snapshot, then drop. No command is
//! // ever sent here — trading controls stay unwired by design.
//! let (_client, welcome, snapshot) =
//!     RemoteClient::connect("wss://gateway/vayren/v1", &token, Duration::from_secs(15))
//!         .expect("gateway reachable");
//! let _ = (welcome, snapshot);
//! ```

pub mod client;
pub mod launch;
pub mod protocol;
pub mod summary;
pub mod token;

pub use client::{Backoff, ClientError, ClientEvent, ConnectionState, RemoteClient};
pub use protocol::{ServerMessage, ERROR_CODES, SCHEMA_VERSION};
pub use summary::summarize_snapshot;
pub use token::{
    resolve_remote_token, TokenError, KEYRING_ACCOUNT, KEYRING_SERVICE, TOKEN_ENV_VAR,
};
