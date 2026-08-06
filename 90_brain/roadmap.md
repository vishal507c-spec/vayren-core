# Roadmap — Platform Kahan Jaa Raha Hai

## 1. Ye kya hai?

Platform ka future plan. Chapter numbers pehle se fixed hain — har naye kaam ka apna ghar hai.

## 2. Phase 1 — Abhi (DONE ✅)

**Goal:** App shuru → SQLite se candles → candlestick chart.

| Kaam | Status |
|---|---|
| Refactor: `00_app / 01_core / 02_market / 03_chart` | ✅ |
| EventBus → `01_core` | ✅ |
| SQLite database + repository + loader | ✅ |
| Chart engine, renderer, widget (zoom/pan/resize), window | ✅ |
| Bootstrap wiring, lifecycle, `python -m app` | ✅ |
| Legacy modules → `99_archive/` | ✅ |

## 3. Future Modules — Fixed Order

Har feature apna module, isi order mein:

```
04_indicator   → indicators
05_drawing     → chart par drawing tools
06_replay      → data replay
07_strategy    → strategies
08_backtest    → backtesting
09_scanner     → market scanning
10_execution   → order execution
11_portfolio   → portfolio tracking
12_broker      → broker integration
13_workspace   → multi-window workspace
14_plugin      → plugin system
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
│  14_plugin                  │
│  13_workspace               │
│  12_broker                  │
│  11_portfolio               │
│  10_execution               │
│  09_scanner                 │
│  08_backtest                │
│  07_strategy                │
│  06_replay                  │
│  05_drawing                 │
│  04_indicator               │
├─────────────────────────────┤
│  03_chart  ← abhi yahan hai │
│  02_market                  │
│  01_core                    │
│  00_app                     │
└─────────────────────────────┘
```

## 7. Ye Kya Nahi Karega

- Phase 2 abhi shuru nahi hua
- Indicator/charting mix nahi honge — alag modules
- Koi module apni jagah se aage nahi badhega

## 8. Example — Naya Module Kaise Aayega

`04_indicator` aane par:

```
1. 04_indicator/indicator/ banao
2. Event catalog mein naya event
3. Module contracts mein API likho
4. make check chalao
5. development_log mein entry
```

> Naksha fixed hai. Har floor ka apna kaam. Dheere dheere building banegi.
