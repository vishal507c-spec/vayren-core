# Architecture — Poora Ghar Ka Naksha

## 1. Ye kya hai?

Ye document batata hai ki **poora system kaise juda hai** — kaun kahan hai, kaun kis se baat karta hai.

## 2. Ek Nazar Mein — Modules

```
00_app ──► 01_core ──► 02_market ──► 03_chart
 (Manager) (Post Office) (Godown)     (Painter)
```

| Module | Kaam | Depend karta hai |
|---|---|---|
| `00_app` | bootstrap, wiring, lifecycle, entry | core, market, chart |
| `01_core` | EventBus, events, logger, registry | kuch nahi |
| `02_market` | SQLite candles: database→repository→loader | core |
| `03_chart` | chart model, engine, renderer, widgets, windows | core, market |

## 3. Ek Module Ke Andar Bhi Layers Hain

```
database   (SQL likhta hai)
    ↓
repository (rows → Bar model)
    ↓
loader     (event sunta hai, event bhejta hai)
    ↓
engine     (data → chart model)
    ↓
widget     (sirf dikhata hai)
```

Har layer ka ek kaam. Layer skip karna — mana.

## 4. Architecture Rules

| Rule | Matlab |
|---|---|
| Event-driven | Sab baat EventBus se |
| Wiring sirf bootstrap mein | Subscriptions sirf `00_app/app/bootstrap/bootstrap.py` |
| No cross-module calls | Module sirf public API se baat karta hai |
| UI bus nahi chhunta | Widgets ko model milta hai, bus nahi |
| SQL sirf market/database mein | Drawing sirf chart/renderer mein |

## 5. Event Flow — Phase 1

```
App.main()
    ↓
Bootstrap.start()  → database connect → AppStarted bhejo
    ↓
AppLifecycle.on_app_started  → LoadSymbol bhejo
    ↓
MarketDataLoader.on_load_symbol → repository → database
    ↓
DataLoaded bhejo
    ↓
ChartEngine.on_data_loaded → ChartModel banao
    ↓
ChartReady bhejo
    ↓
ChartWindow.on_chart_ready → widget.set_model → show
    ↓
WindowRendered bhejo
    ↓
AppLifecycle.on_window_rendered (bas, khatam)
    ↓
Qt event loop
```

**Bus synchronous hai** — matlab poora chain ek saath chalta hai, Qt loop shuru hone se pehle hi.

## 6. Story

Socho ek office hai:

- **Manager** (App) subah aata hai, sabko order deta hai
- **Post office** (EventBus) har message pass karta hai
- **Godown ka munshi** (Market loader) godown (SQLite) se data nikalta hai
- **Painter** (Chart engine + widget) wall par chart banata hai

Manager khud godown nahi jata. Wo post office se kahta hai — *"SPY ka data bhejo"*.

## 7. Example — Dependency Chain

```
chart imports core + market        ✅
market imports core                ✅
core imports kuch nahi             ✅
app imports core + market + chart  ✅ (manager ko sabse baat karni hai)
chart imports app                  ❌ (peeche jaana mana)
```

## 8. Enforcement

`scripts/validate_imports.py` dependency map check karta hai (AST se).

`scripts/validate_structure.py` layout check karta hai.

Dono `make check` ka hissa hain.

## 9. Future

Naya module aayega toh ye chain aage badhegi — `04_indicator → 05_drawing → …` — architecture wahi rahega.

> Naksha yaad rakho: Manager → Post Office → Godown → Painter.
