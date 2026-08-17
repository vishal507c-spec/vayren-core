# Roadmap — Platform Kahan Jaa Raha Hai

## 1. Ye kya hai?

Platform ka future plan. Chapter numbers pehle se fixed hain — har naye kaam ka apna ghar hai.

## 2. Phase 1 — Abhi (DONE ✅)

**Goal:** App shuru → SQLite se candles → candlestick chart.

| Kaam | Status |
|---|---|
| Refactor: `00_app / 01_core / 02_data / 03_market / 04_chart` | ✅ |
| EventBus → `01_core` | ✅ |
| SQLite database + repository + loader | ✅ |
| Chart engine, renderer, widget (zoom/pan/resize), window | ✅ |
| Bootstrap wiring, lifecycle, `python -m app` | ✅ |
| Legacy modules → `99_archive/` | ✅ |
| **02_data: Historical download engine (Phase 6)** | ✅ (2026-08-14) — ported KITE-CANDLE-DOWNLOAD, worker thread + UI + events + manifest |

## 3. Future Modules — Fixed Order

Har feature apna module, isi order mein — development story: data → market → chart → **strategy → backtest → risk → execution → portfolio** (phir tooling):

```
05_strategy   → trading strategy logic
06_backtest   → historical strategy testing
07_risk       → risk management
08_execution  → order execution
09_portfolio  → portfolio/position management
10_scanner    → market scanning
11_indicator  → indicators
12_drawing    → chart par drawing tools
13_replay     → data replay
14_workspace  → multi-window workspace
15_plugin     → plugin system
```

## 4. Har Naye Phase Ke Pakke Rules

| Rule | Matlab |
|---|---|
| Dependency sirf neeche | Naya module sirf chhote numbers ke public APIs se |
| Event-driven | Sirf EventBus se baat |
| Apna module, apna kaam | Existing module expand nahi hota |
| Contract likho | Naya module aaye → `module_contracts.md` + `event_catalog.md` update |

## 5. Story

Socho ghar ki building ban rahi hai.

Pehle sirf ground floor — ek kamra, ek kaam (chart).

Building ka naksha pehle se bana hai — har floor ka naam fixed hai.

Floor 4 par indicator aayega, floor 12 par broker. Naksha badalne ki zaroorat nahi — bas floors bante jayenge.

## 6. Diagram — Building Ka Naksha

```
┌─────────────────────────────┐
│  15_plugin                  │
│  14_workspace               │
│  13_replay                  │
│  12_drawing                 │
│  11_indicator               │
│  10_scanner                 │
│  09_portfolio               │
│  08_execution               │
│  07_risk                    │
│  06_backtest                │
│  05_strategy                │
├─────────────────────────────┤
│  04_chart     ← abhi yahan hai│
│  03_market                   │
│  02_data                     │
│  01_core                     │
│  00_app                      │
└─────────────────────────────┘
```

## 7. Ye Kya Nahi Karega

- Phase 2 abhi shuru nahi hua
- Indicator/charting mix nahi honge — alag modules
- Koi module apni jagah se aage nahi badhega

## 8. Example — Naya Module Kaise Aayega

`05_strategy` aane par:

```
1. 05_strategy/strategy/ banao
2. Event catalog mein naya event
3. Module contracts mein API likho
4. make check chalao
5. development_log mein entry
```

> Naksha fixed hai. Har floor ka apna kaam. Dheere dheere building banegi.
