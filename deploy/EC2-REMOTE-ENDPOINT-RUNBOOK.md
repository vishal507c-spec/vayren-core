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

## 5. SAFE connectivity test (no trading — read + rejected-write only)

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

## 6. EXE/APK client requirements (remaining work, client side)

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

## 7. Rollback

`sudo systemctl stop vayren-remote`; remove the 443 SG rule. The backend,
strategies, risk, and broker adapters are untouched by this entire setup,
so stopping the transport restores the previous state exactly.
