# market/ — Market Data

## What Is Market Data?

**Market data** is the raw information from financial exchanges: prices, trades, order books, and every other piece of data that tells us what is happening in the market.

When you look at a stock chart on Google Finance and see:

```
SPY: $450.50 (+0.5%)
Open: $448.00  High: $452.00  Low: $447.50  Close: $450.50
Volume: 45,200,000
```

That is **market data**. It is the raw information about what happened in the market.

---

## Purpose

`market/` is where raw market data **enters the system**. Every price, trade, and order book snapshot comes through here first, gets validated, normalized into our standard format, and then sent to the rest of the system.

---

## Why This Folder Exists

Every decision in the trading system starts with market data:

- A strategy decides to buy because "the price crossed above the moving average"
- Risk calculates that "the portfolio is 50% exposed to tech stocks"
- Analytics measures that "the strategy returned 12% this month"

All of these depend on accurate, timely market data. `market/` is the gatekeeper that ensures data quality before anything else uses it.

---

## What Problem Does It Solve?

**Data inconsistency.** Different data sources (Polygon, IEX, CSV files) all send data in different formats. Polygon calls the open price `"o"`, IEX calls it `"open"`, and a CSV file might call it `"Open"`.

Without `market/`, every downstream system would need to understand every data source's format. With `market/`, all sources are normalized into our standard `Bar` model before anything else sees the data.

---

## Mental Model

Think of `market/` as the **loading dock** of a warehouse.

Trucks arrive from different suppliers (Polygon, IEX, CSV files). Each truck has boxes in a different arrangement. The loading dock unloads every truck, repacks everything into our standard boxes, and puts them on the conveyor belt for the rest of the warehouse.

The rest of the warehouse never needs to know what the trucks looked like. They only see our standard boxes.

---

## Real World Analogy

A **translator at the United Nations**. Speakers from different countries speak different languages. The translator (adapter) converts every speech into a standard format so all participants can understand.

---

## Visual Diagram

```
    DATA SOURCES                 market/                       REST OF SYSTEM
    
    ┌────────────┐
    │ Polygon.io │───┐
    └────────────┘   │   ┌────────────────┐    ┌──────────┐   ┌──────────┐
                     ├──▶│   Adapters     │───▶│ Services │──▶│  Events  │──▶ signals/
    ┌────────────┐   │   │ (normalize raw │   │ (ingest, │   │ (announce│   strategies/
    │  IEX Cloud │───┘   │  data into our │   │  query,  │   │  new     │   execution/
    └────────────┘       │   model format)│   │ validate)│   │  data)   │   portfolio/
                         └────────────────┘   └──────────┘   └──────────┘   risk/
    ┌────────────┐
    │ CSV Import │──── Convert every source                      analytics/
    └────────────┘    to Bar, Trade, OrderBook
```

---

## Where It Fits in the System

`market/` is the **first stage of the data pipeline**. Everything downstream depends on the quality of data produced here.

```
Position in the pipeline:  1st of 8 processing departments
Before this:               lib/ (provides the types used by market models)
After this:                signals/ (consumes market data to compute signals)
```

---

## What Comes Before It

`lib/` — the types (Currency, Timestamp) and utilities used by market models.

---

## What Comes After It

`signals/` — the next department in the pipeline. Signals reads market data and transforms it into trading signals.

---

## Folder Structure

```
market/
├── __init__.py      ← Public API: Bar, Trade, OrderBook, MarketDataQuery, events
├── README.md        ← This lesson
├── models/          ← Things: Bar, Trade, OrderBook, Exchange, Symbol
├── services/        ← Actions: ingestion, query, data quality
├── events/          ← Messages: BarReceived, TradeReceived
├── adapters/        ← Connections: Polygon, IEX, CSV import
├── tests/           ← Checks: verify everything works correctly
└── conftest.py      ← Shared test data: sample bars for testing
```

---

## What Every Subfolder Means

| Subfolder | Purpose | Key Files |
|---|---|---|
| `models/` | **Things** — structured representations of real market data | `bar.py`, `trade.py`, `order_book.py`, `exchange.py`, `symbol.py` |
| `services/` | **Actions** — operations you can perform on models | `ingestion.py`, `query.py`, `quality.py` |
| `events/` | **Messages** — announcements that new data is available | `bar_received.py`, `trade_received.py` |
| `adapters/` | **Connections** — bridges to external data sources | `polygon.py`, `iex.py`, `csv_import.py` |

---

## What Every Important File Means

| File | What It Defines | Why It Exists |
|---|---|---|
| `models/bar.py` | `Bar` — OHLCV price summary over a time period | The most common form of market data. Every strategy starts here. |
| `models/trade.py` | `Trade` — a single transaction | Fine-grained data for detailed analysis. |
| `models/order_book.py` | `OrderBook`, `OrderBookLevel` — all current bids and asks | Required for understanding market depth and execution quality. |
| `models/exchange.py` | `Exchange` — a marketplace with hours, timezone, etc. | Different exchanges have different rules and hours. |
| `models/symbol.py` | `Symbol` — a traded asset with metadata | Every piece of data is associated with a symbol. |
| `services/ingestion.py` | `MarketDataIngestion` — normalizes raw data into models | Converts different source formats into our standard models. |
| `services/query.py` | `MarketDataQuery` — retrieves stored data | How other departments ask for historical or current data. |
| `services/quality.py` | `DataQualityService` — validates data correctness | Catches bad data before it propagates downstream. |
| `events/bar_received.py` | `BarReceived` — announced when a bar is ingested | Tells other departments "new data is available." |
| `events/trade_received.py` | `TradeReceived` — announced when a trade is ingested | Same as above for trade data. |

---

## Data Flow

```
1. Data arrives from external source (Polygon, IEX, CSV)
       │
       ▼
2. Adapter converts raw format to our standard model
       │
       ▼
3. Service validates and ingests the data
       │
       ▼
4. Event is published: "BarReceived" or "TradeReceived"
       │
       ▼
5. Downstream departments (signals, strategies, etc.) react to the event
```

---

## Dependency Flow

```
market/ depends on:
  ├── lib/ (Currency, Timestamp, Logger, etc.)
  └── Python standard library + third-party packages (httpx, pandas)

market/ does NOT depend on:
  └── Any other department inside the project
```

---

## Typical Workflow

```python
# 1. Data arrives from Polygon
adapter = PolygonAdapter(api_key="...")
raw_data = adapter.get_bars("SPY")

# 2. Normalize into our format
ingestion = MarketDataIngestion()
for raw in raw_data:
    bar = ingestion.normalize_bar(raw, source="polygon")

# 3. Validate quality
quality = DataQualityService()
if quality.is_bar_valid(bar):
    print(f"Valid bar: {bar.symbol} {bar.close}")

# 4. Query stored data
query = MarketDataQuery()
query.add_bars("SPY", [bar])
recent = query.get_bars("SPY", limit=20)
```

---

## Quick Example

```python
from market import Bar

# Create a bar
bar = Bar(
    symbol="SPY",
    open=448.00,
    high=452.00,
    low=447.50,
    close=450.50,
    volume=45200000,
    timestamp="2025-07-05T20:00:00Z",
)

# Read properties
print(bar.range)          # 4.50 (high - low)
print(bar.is_bullish)     # True (close >= open)
print(bar.return_pct)     # 0.56% (percentage change)
print(bar.typical_price)  # 450.17 (average of high, low, close)
```

---

## Common Mistakes

| Mistake | Why It Is Wrong |
|---|---|
| Creating market models in other departments | Every department should use the same `Bar` model. Define it once in `market/models/`. |
| Forgetting time zones | Always use UTC internally. Convert to local time only for display. |
| Trusting data without validation | Exchanges send bad data. Always run it through `DataQualityService`. |
| Not handling missing data | Markets close, data feeds fail, symbols are delisted. `Nullable` handles missing values. |
| Mixing data sources without normalization | Always normalize through adapters before using data. |

---

## Industry Best Practices

| Practice | Why |
|---|---|
| **Normalize at ingestion** | Convert all external formats to internal models at the boundary. Never let raw external formats propagate. |
| **Validate early** | Catch bad data at the loading dock, not in the strategy engine. |
| **Immutable models** | Once created, a Bar never changes. This prevents accidental modification. |
| **Always UTC** | Every timestamp is UTC. Timezone conversion happens only at display time. |
| **Source tracking** | Every Bar remembers its source ("polygon", "iex", "csv"). Useful for debugging data quality issues. |

---

## Engineering Notes

`market/` follows the **adapter pattern**: adapters convert external interfaces to internal ones. This means:
- Adding a new data source = writing one new adapter
- An adapter change never affects downstream code
- Different data sources can be swapped at configuration time

The `DataQualityService` is a **defense in depth** measure. Even if an adapter has a bug, the quality check catches it before bad data propagates.

---

## Future Growth

As the system grows, `market/` may add:
- **Real-time streaming** (WebSocket connections for live data)
- **Database persistence** (storing data in a database instead of in-memory)
- **Data reconciliation** (comparing data from multiple sources for accuracy)
- **Corporate actions** (splits, dividends, mergers)
- **Fundamental data** (earnings, financial statements)

Each addition is a new adapter or a new service. The folder structure already accommodates this.

---

## What Should NEVER Go Here

| ❌ Never Put This In `market/` | Where Does It Go? |
|---|---|
| Trading logic | `strategies/` |
| Signal computation | `signals/` |
| Order management | `execution/` |
| Performance analysis | `analytics/` |
| AI/ML models | `research/` |

---

## Summary

`market/` is the **data loading dock** of the trading system.

- It receives raw data from multiple sources
- Normalizes everything into standard models (Bar, Trade, OrderBook)
- Validates quality before passing data on
- Announces new data via events (BarReceived, TradeReceived)
- Provides query services for historical data

Every department downstream depends on the clean, normalized data that `market/` produces.

---

## Continue to the Next Lesson

→ `signals/` — How market data becomes trading signals
