# Windows packaged remote client — build + one-time setup

The shipped `vayren-shell.exe` connects to the EC2 gateway on its own: no
`--remote-ui` flag, no pasted URL, no per-launch token step, and no dependence
on any shortcut's arguments. Configuration lives in exactly two places: the
built-in default endpoint (compiled in) and the OS credential store.

## 1. Build the packaged EXE (on the Windows host)

```bat
cargo build --release -p vayren-shell --features packaged-remote
```

The `packaged-remote` feature flips the default to remote
(`crates/vayren-remote-client/src/launch.rs::resolve_launch_mode`). Explicit
flags are untouched: `--remote-ui[=url]` and `--remote[=url]` behave as before,
`--local` forces the developer loop, and legacy shortcuts carrying
`--data-dir` / `--strategy-dir` connect to remote automatically unless `--local`
is explicitly given. Rebuilding or replacing the EXE changes nothing about
stored credentials.

## 2. One-time credential provisioning (per Windows device, once ever)

Store the remote token as a **generic credential**:

- Target: `remote-token.vayren-remote`
- Username: `remote-token`
- Password: the remote token (copy from the EC2 host's provisioned
  `VAYREN_REMOTE_TOKEN`; never commit it, never put it in a shortcut,
  batch file, or environment permanently)

Secure command-line form (prompts without echo):

```bat
cmdkey /generic:remote-token.vayren-remote /user:remote-token
```

Verify presence without revealing the secret:

```bat
cmdkey /list:remote-token.vayren-remote
```

The target-name shape (`user.service`) is the `windows-native-keyring-store`
default the EXE reads via `keyring::Entry::new("vayren-remote", "remote-token")`.

## 3. Shortcut (convenience only — carries zero configuration)

Target: `<exe-dir>\vayren-shell.exe` with **no arguments**.
Start in: `<exe-dir>` (the folder containing the EXE).
Name suggestion: `VAYREN Remote`.

Deleting and recreating the shortcut changes nothing: the app owns the
endpoint (built-in default, `VAYREN_REMOTE_URL` only as intentional override)
and the credential (OS store slot above).

## 4. Transport

Tailscale must be installed with the `Tailscale` service `Running` +
`Automatic`. The app treats transport as transport: unreachable gateway reads
`OFFLINE`/`CONNECTING` with automatic retry, never a permanent failure.

## 5. What the operator sees

First run unprovisioned: `CONNECTING` → `OFFLINE` + one-time setup note; the
bootstrap retries every few seconds. Provision the credential once → the
running app connects itself (`AUTHENTICATING` → `CONNECTED` + EC2 snapshot),
and every later launch/restart/reconnect is automatic, including after
temporary network loss, sleep/wake, or gateway restart (`subscribe{last_seq}`
resume, `resync:true` full replace — unchanged semantics).

The client is read-only by construction: no command is ever sent and START
stays inert. Placing orders is out of scope for this path.

## 6. Windows verification (manual, same device)

A. Close EXE. B. Delete the Desktop shortcut. C. Open the EXE directly
(double-click in Explorer). D. Must auto-connect. E. Create a fresh shortcut
(bare target). F. Launch again. G. Must auto-connect. H. Replace/rebuild the
EXE and repeat. I. Must still auto-connect on the same OS credential.
