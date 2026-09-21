# Broker — Unified Broker Layer (FINAL production architecture)

One coordination boundary between VAYREN and any venue. Current truth: this
package + `90_brain/module_contracts.md` §5.10.

## Layout

```
broker/
  vocab.py         unified ErrorCode + typed errors + legacy translation
  capabilities.py  CapabilitySet + Caps ids + tri-state CapabilityStatus (M8)
  faces.py         HistoricalFace / MarketDataFace / TradingFace / BrokerPlugin
                   + StaticPlugin / FactoryPlugin implementations
  registry.py      BrokerRegistry — the ONLY name→plugin map
  selection.py     BrokerSelection + SelectionStore + surface_resolution
                   + tri-state surface_status (M8)
  selection_store.py  FileSelectionStore — atomic JSON persistence (M4)
  funds.py          FundsSnapshot + require_funds over TradingFace.funds (M6)
  credentials.py    CredentialRef/Scope/Metadata/Resolver + validate fns (M8/FINAL)
  health.py         HealthState + BrokerHealth + legacy wrappers (M8)
  identity.py      BrokerIdentity + identity_of (FINAL)
  adapters/skeleton/  reference template: all faces fail-closed (FINAL)
  tests/           contract tests (M1–FINAL, incl. generic adapter harness)
```

## Rules (from the approved design)

- Core, strategy, backtest, risk, chart, market never import `broker`.
- `data.provider.factory` resolves history through the registry (same
  instances, same errors, same sentinel identity; unknown names fail closed).
- Capability absent → fail-closed (`UnsupportedCapabilityError` /
  `NotConfiguredError` / explicit ValueError). No silent fallback.
- Tri-state declaration (M8): SUPPORTED / NOT_SUPPORTED / NOT_CONFIGURED —
  bool helpers preserved for compatibility.
- No `if broker == ...` anywhere; branching is capability-based.
- Real broker SDKs may only appear inside isolated adapter packages;
  network clients only inside `09_broker/broker/adapters/` or the retained
  historical transport `02_data/data/provider/` (validator-enforced).
- Credentials are key-references only (UBL `credentials.py`); values never
  in selection, logs, events, journals or UI.
- Health is connection-only and never implies LIVE readiness.

## Status

FINAL production architecture — no future broker phases. M1–M8 complete
plus: typed `BrokerIdentity`; credential lifecycle (broker/scope binding,
expiry/rotation metadata); market-data full lifecycle
(connect/disconnect/reconnect); `RECONCILING` startup path with engine
idempotency survival; SAFE/WARNING/BLOCKED reconciliation verdicts;
timeout/backoff/reconnect policies; skeleton reference adapter; 12-step
activation ceremony (pure verifier); 20-point architecture proofs.
M6 funds and M7 shim decisions preserved verbatim. Zerodha stays
history-only (trading/funds NOT_SUPPORTED). LIVE stays NOT_CONFIGURED —
only external operational prerequisites remain (account, credentials,
permissions, KYC, endpoint, operator consent).
