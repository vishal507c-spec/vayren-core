# App — Manager (00_app)

`00_app/app/` — application ka **composition package**. Production entry
Rust + Slint shell hai (`make dev` → `scripts/launch_native.py` → the
`vayren-shell` binary), served by headless Python backend (`app/headless.py`).

## Andar Kya Hai
| Cheez | Kaam |
|---|---|
| `headless.py` | Headless backend: stdin/stdout par newline-delimited JSON (ready/symbols/market/system/portfolio/live/research/lab/shutdown) |
| `services/` | Composition services — broker manager/selection, live trading, research (koi UI toolkit nahi) |

## Wiring — Single Source
Har screen ka snapshot backend se aata hai; Rust shell `MarketState`/`BrokerWorkspace`/`LiveState`/`ResearchState`/`LabState`/`PortfolioState` me ingest karke Slint par project karta hai. Python business logic kabhi paint nahi karta.

Event flow: backend snapshot → bridge JSON → Rust state → Slint projection (sync, fail-closed).

## Example
```bash
make dev -- --symbol RELIANCE --timeframe 15m --limit 200 --screen chart
VAYREN_DATA_DIR=D:\ZerodhaTradingData make dev
```

## Ye Kya Nahi Karega
Business logic / SQL / painting — sab respective modules mein. Yahan sirf composition + IPC protocol.
