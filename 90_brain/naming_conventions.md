# Naming Conventions — Naam Se Pehchaan

## 1. Ye kya hai?

Har cheez ka naam kaise rakhna hai — ye table. Naam sahi ho toh code khud bolta hai.

## 2. Main Table

| Cheez | Rule | Example |
|---|---|---|
| Chapters | Numbered (story order), lower_snake | `00_app`, `01_core`, `02_data`, `03_market`, `04_chart` |
| Python packages | Lowercase, singular | `market`, `chart` |
| Python files | snake_case | `candle_repository.py`, `market_data_loader.py` |
| Classes | PascalCase | `CandleChartWidget`, `SqliteCandleDatabase` |
| Functions/methods | snake_case | `get_candles()`, `on_load_symbol()` |
| Events | Past tense, PascalCase | `AppStarted`, `DataLoaded`, `ChartReady`, `WindowRendered` |
| Requests (commands) | Imperative, PascalCase | `LoadSymbol` (catalog ka akela command) |
| Constants | UPPER_SNAKE | `MIN_VISIBLE_BARS`, `ZOOM_STEP` |
| Tests | `test_` prefix file + function | `test_chart_engine.py::test_sorts_bars_ascending` |

## 3. Naam Ke Matlab

### `on_<event>` — Event Handler

```
on_load_symbol   → LoadSymbol aane par ye chalta hai
on_data_loaded   → DataLoaded aane par ye chalta hai
```

- Subscribe bootstrap karta hai
- Seedha call kabhi nahi hota

### `set_model()` — Widget Model Le Leta Hai

```
set_model(model) → widget ko naya data mila
```

Widgets models rakhte hain, events nahi.

### Module Naam = Cheez Ka Naam

| Module | Naam kyu |
|---|---|
| `market` | Ye KYA hai — data ka godown |
| `chart` | Ye KYA hai — painter |

Kaam ke naam nahi (`loader`, `displayer`) — cheez ke naam.

### Layers Ke Naam = Ek Kaam

| Layer | Kaam |
|---|---|
| `database` | SQL |
| `repository` | Mapping (rows → models) |
| `loader` | Event-driven loading |
| `engine` | Model preparation |
| `renderer` | Painting |
| `widgets` | Viewports |
| `windows` | Hosts |

## 4. Story

Socho ek village mein har ghar ka naam hai: "Sharma House", "Chai Point".

Koi pooche — "Sharma House kahan hai?" → turant pata chal jata hai.

Agar naam "Building Number 3" hota toh kuch samajh nahi aata.

Naam hi pehchaan hai.

## 5. Example — Accha vs Kharab Naam

| ❌ Kharab | ✅ Accha |
|---|---|
| `DataManager` | `MarketDataLoader` |
| `RenderCandles` (function) | `paint()` method in `CandleRenderer` |
| `graph.py` | `candle_chart_widget.py` |
| `do_stuff()` | `on_load_symbol()` |

## 6. Future

Naye modules (`05_strategy`...) isi table ke rules follow karenge.

> Naam sahi → code khud document. Naam galat → koi samjhega nahi.
