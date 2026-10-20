# EC2 secure remote endpoint — operator runbook

Public contract: `wss://<domain>/vayren/v1` → proxy → `ws://127.0.0.1:8765/vayren/v1`.
Backend bind never leaves loopback. Port 8765 is never published.

## 0. Preconditions (EC2 host)

- Ubuntu host with the repo at `/opt/vayren-core`, venv present, port 8765 free.
- A DNS A record `<domain>` → the instance's public IP.
- You hold AWS console/CLI rights for exactly one Security Group change.

## 1. Security Group (AWS console or CLI) — minimum ports

- ALLOW inbound `443/tcp` from `0.0.0.0/0` (and `::/0` if IPv6 is used).
- ALLOW inbound `80/tcp` from `0.0.0.0/0` (ACME HTTP-01 + redirect only).
- ALLOW inbound `22/tcp` from your admin IP only (existing practice, unchanged).
- DO NOT add any rule for `8765/tcp`. Verify afterwards: no rule may
  reference port 8765 in any SG attached to the instance.

## 2. Tokens (on EC2, never in git)

```bash
sudo mkdir -p /etc/vayren && sudo chown vayren:vayren /etc/vayren && chmod 700 /etc/vayren
python3 -c "import secrets; print(secrets.token_urlsafe(32))"   # → VAYREN_REMOTE_TOKEN (EXE operator)
python3 -c "import secrets; print(secrets.token_urlsafe(32))"   # → VAYREN_REMOTE_READ_TOKEN (APK display, optional)
sudo cp deploy/vayren-remote.env.example /etc/vayren/remote.env
sudo vi /etc/vayren/remote.env   # paste the two tokens, keep HOST=127.0.0.1
sudo chown vayren:vayren /etc/vayren/remote.env && chmod 600 /etc/vayren/remote.env
```

Broker credentials stay in the broker manager's own store/vault. Nothing
FYERS-shaped (app id, secret, TOTP, access token) goes into `remote.env`,
the token file, nginx config, or logs.

## 3. TLS (Let's Encrypt, certs stay on the host)

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d <domain>   # obtains + installs fullchain/privkey
```

`deploy/nginx-vayren-remote.conf.example` references
`/etc/letsencrypt/live/<domain>/{fullchain.pem,privkey.key}` — replace
`<domain>`, install the `server{}` block, then `sudo nginx -t && sudo systemctl reload nginx`.
No certificate or key material is ever committed to the repo.

## 4. Backend service (transport only — no trading)

```bash
sudo cp deploy/vayren-remote.service.example /etc/systemd/system/vayren-remote.service
sudo systemctl daemon-reload && sudo systemctl enable --now vayren-remote
sudo systemctl status vayren-remote --no-pager   # must be active (running), idling
sudo ss -tlnp | grep 8765                        # must show 127.0.0.1:8765 ONLY
```

### 4.1 Daily FYERS token lifecycle (read before LIVE START)

FYERS access tokens are **daily** credentials. Recovery differs depending on
when the token expires:

- **Gateway startup / explicit CONNECT** — the saved token is validated; if
  it is expired and auto-authentication (PIN/TOTP) is configured for the
  venue, the manager acquires and verifies a new token automatically. If
  auto-auth is not configured, the operator completes the venue's own
  interactive login from SYSTEM → BROKERS.
- **Gateway already running** — expiry is detected by the bounded health
  refresh (at most one queued check per ~60 s while snapshots are being
  read; this is not a continuous poll or an in-process token renewer). The
  authoritative session moves to `LOGIN_REQUIRED` and LIVE readiness
  blocks. The operator must click **CONNECT** in SYSTEM → BROKERS (which
  runs the same auto-auth flow when configured), complete interactive
  login, or use the authorized restart path in §5 — after which the
  startup check re-validates the saved token.

Do not claim or assume continuous automatic refresh while the gateway is
running; no such background token renewal is performed.

LIVE START itself performs a fresh, bounded proof before accepting the
request: a forced read-only profile check, a bounded order-WebSocket
verification, and an account/funds refresh. A cached `CONNECTED` snapshot
is not accepted. If the order stream is disconnected, stale, or cannot be
proven within the timeout, START fails closed with an exact blocker. An
explicit START may make one bounded cold-start attempt after a cooldown,
but it never runs its own retry loop. Outside market hours the venue may
close the order socket; LIVE START stays blocked until the stream can be
verified. Token values, PINs and secrets are never printed or logged.

## 5. Operator-authorized gateway restart (sandbox-safe helper)

The gateway runs with `NoNewPrivileges=true` (see §4), so it can never
`sudo`. A restart command therefore answers `RESTART_UNAVAILABLE: gateway is
sandboxed (NoNewPrivileges=1) ...` unless the narrowly scoped helper below
is installed. Two authorized restart paths exist; no other privilege is
granted or weakened either way:

1. **Operator SSH / ops CLI** — the operator's own sudo policy:
   `sudo systemctl restart vayren-remote.service`
2. **Client restart command** (optional, auditable) — install the helper
   units so the sandboxed gateway can *request* exactly one operation:

```bash
cd /home/ubuntu/vayren-core
sudo cp deploy/vayren-remote-restart.service.example /etc/systemd/system/vayren-remote-restart.service
sudo cp deploy/vayren-remote-restart.path.example    /etc/systemd/system/vayren-remote-restart.path
sudo systemd-analyze verify /etc/systemd/system/vayren-remote-restart.service
sudo systemctl daemon-reload
sudo systemctl enable --now vayren-remote-restart.path
systemctl status vayren-remote-restart.path --no-pager   # active (waiting)
# Upgrading an existing install: re-copy the service unit (symlink-safe,
# root-only audit copy) and `sudo systemctl daemon-reload`; the path unit is
# unchanged. The StateDirectory is created automatically on first run.
```

Security boundary (do not weaken):

- The gateway still runs with `NoNewPrivileges=true` and no sudo rights.
- It can only write `<data-dir>/restart.request` (data only). The path unit
  watches exactly `PathExists=/home/ubuntu/.vayren/data/restart.request`.
- The helper service (`vayren-remote-restart.service`) has one fixed
  `ExecStart`: `systemctl restart vayren-remote.service`. It has no shell,
  takes no caller input, and can control no other unit. The request file is
  never parsed or executed — it is copied for audit, then removed so the path
  unit rearms. The copy is symlink-safe and stored in a root-only
  `StateDirectory` (`/var/lib/vayren-restart`, mode 0700): any previous
  `.applied` is unlinked first, source symlinks are not followed, and the
  gateway cannot race/replace the destination because it cannot write there.
- A gateway started against any other data directory fails closed: it
  refuses to write a marker the helper would never action.
- Active-session protection is unchanged: a RUNNING live session blocks
  restart with `RESTART_BLOCKED` before any request is written.

Verification after install (marker is data only; safe to repeat):

```bash
printf '{"source":"operator-ssh-check"}\n' | sudo tee /home/ubuntu/.vayren/data/restart.request
sleep 3
systemctl is-active vayren-remote                     # active
journalctl -t vayren-restart -n 5 --no-pager          # completion line
sudo cat /var/lib/vayren-restart/restart.request.applied
```

Audit trail: the client command is recorded in
`/home/ubuntu/.vayren/data/audit_ops.log` (caller role + connection id), and
the applied request is kept in
`/var/lib/vayren-restart/restart.request.applied` (root-only). To remove the
helper entirely: `sudo systemctl disable --now vayren-remote-restart.path &&
sudo rm /etc/systemd/system/vayren-remote-restart.{path,service} &&
sudo systemctl daemon-reload`.

## 6. SAFE connectivity test (no trading — read + rejected-write only)

From any machine with `python3` (no VAYREN code needed), open a WebSocket to
`wss://<domain>/vayren/v1`:

1. Send `{"type":"hello","v":1,"payload":{"token":"WRONG"}}` → expect an
   `error` with `code: AUTH_FAILED`. The server must NOT send a snapshot.
2. Send `hello` with the real token → expect `welcome` (role) then a
   `snapshot` whose `strategy.status` is `STOPPED`/`BLOCKED` (never RUNNING
   unless a session was deliberately started earlier through the gates).
3. Send `ping` → expect `pong`.
4. With the READ token, send any `command` → expect `NOT_AUTHORIZED` and no
   state change.
5. STOP. Do not send `start`. Do not touch orders. Confirm the service log
   shows no session start.

## 7. EXE/APK client requirements (remaining work, client side)

- Target `wss://<domain>/vayren/v1`, schema `v: 1`.
- Safe smoke test from any terminal (Windows EXE or this repo), read-only,
  never sends a command:
  `vayren-shell.exe --remote wss://<domain>/vayren/v1`
  (or `cargo run -p vayren-remote-client --example remote_ping -- <url>`)
  with `VAYREN_REMOTE_TOKEN` in the environment. Expect `welcome`
  (connection + role), the snapshot summary, then sequenced arrivals.
- First frame must be `hello{token}` (fail fast on `AUTH_FAILED`).
- Render `snapshot`, then apply `state_update` sections and `event` frames by `seq`.
- Reconnect with `subscribe{channels, last_seq}`; on `resync:true`, replace state.
- Every control command carries a client-generated `request_id`; on retry,
  reuse the SAME id (server replays the cached result).
- Keep the token in OS secure storage; never log it; never ship broker keys.

## 8. Rollback

`sudo systemctl stop vayren-remote`; remove the 443 SG rule. The backend,
strategies, risk, and broker adapters are untouched by this entire setup,
so stopping the transport restores the previous state exactly.
