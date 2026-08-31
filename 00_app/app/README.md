# App — Manager (00_app)

`00_app/app/` — application ka **entry point**. Bus + services + wiring ka single owner.

## Andar Kya Hai
| Cheez | Kaam |
|---|---|
| `App` | Entry: args → logging → QApplication → bootstrap → Qt loop |
| `Bootstrap` | Bus + services + `ComponentRegistry`/`SystemModel` banata hai; **subscriptions sirf yahan**; `start()` = window show + `AppStarted` |
| `AppLifecycle` | `AppStarted` → `ListSymbols`; `WindowRendered` → terminal |

## Wiring — Single Source
Subscriptions sirf `00_app/app/bootstrap/bootstrap.py` mein. Poora event wiring (19 events) `90_brain/event_catalog.md` mein authoritative hai. Yahan duplicative list nahi — `Bootstrap` hi composition root hai.

Event flow: `AppStarted → ListSymbols → SymbolsListed → QuotesLoaded → LoadSymbol/TimeframeChanged → DataLoaded → ChartReady → WindowRendered` (sync bus, Qt loop se pehle complete).

## Example
```bash
python -m app --data-dir D:\ZerodhaTradingData --limit 5000
VAYREN_DATA_DIR=D:\ZerodhaTradingData python -m app
vayren  # console script
```

## Ye Kya Nahi Karega
Business logic / SQL / painting — sab respective modules mein. Yahan sirf wire + lifecycle.
