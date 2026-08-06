# App — Manager

## 1. Ye kya hai?

`00_app/app/` — application ka **entry point**. Sab yahin se shuru hota hai.

## 2. Andar Kya Hai

| Cheez | Kaam |
|---|---|
| `App` | Entry point: args → logging → QApplication → bootstrap → Qt loop |
| `Bootstrap` | Bus + services banata hai; **subscriptions sirf yahan**; `start()` = connect + `AppStarted` |
| `AppLifecycle` | `AppStarted` aaye → `LoadSymbol` bhejo; `WindowRendered` aaye → bas, ready |

## 3. Story

Socho ek office. Subah manager aata hai.

1. Sab lights on karta hai (QApplication)
2. Sab departments ready karta hai (Bootstrap)
3. *"Shuru karo!"* bolta hai (AppStarted)
4. Sab kaam khud hota hai — manager sirf dekhta hai
5. Jab window khul jati hai (WindowRendered), manager baith jata hai (Qt loop)

## 4. Diagram

```
App.main()
    ↓
args + logging + QApplication
    ↓
Bootstrap (services + subscriptions)
    ↓
bootstrap.start()
    ↓
AppStarted → LoadSymbol → ... → WindowRendered
    ↓
Qt event loop (manager baitha hai)
```

## 5. Subscriptions Sirf Yahan

```
AppStarted    → AppLifecycle.on_app_started
LoadSymbol    → MarketDataLoader.on_load_symbol
DataLoaded    → ChartEngine.on_data_loaded
ChartReady    → ChartWindow.on_chart_ready
WindowRendered → AppLifecycle.on_window_rendered
```

Ye 5 lines poore platform ka wiring hai. Kahin aur subscribe — mana.

## 6. Example — Kaise Chalayein

```bash
python -m app                          # default: SPY, data/vayren.db
python -m app --symbol AAPL --db my.db --limit 1000 --log-level DEBUG
vayren                                # console script (setup ke baad)
```

Environment se bhi:

```
VAYREN_SYMBOL=QQQ VAYREN_DB=data/q.db python -m app
```

## 7. Ye Kya Nahi Karega

| ❌ Nahi karega | Kyu |
|---|---|
| Business logic | Manager kaam karta hai, kaam kisi aur ka hai |
| SQL | Database `02_market` ki jagah hai |
| Chart painting | Painter `03_chart` mein hai |
| Subscribe kisi aur jagah | Wiring sirf Bootstrap ki |

## 8. Future

Naya module aayega → `Bootstrap` mein ek nayi subscription line. Bas.

> Manager = shuru karo, wire karo, baith jao.
