# market/models/ — Models

## What Are Models?

**Models** are structured representations of real-world things. They define **what exists** in a domain — the nouns of the system.

If you were building software for a library, your models would include `Book`, `Patron`, and `Loan`. For a trading system, our models include `Bar`, `Trade`, and `OrderBook`.

Models are **frozen** (immutable) — once created, they cannot change. This prevents bugs where data is accidentally modified.

---

## What Is Inside

| Model | Real-World Thing | Key Fields |
|---|---|---|
| `Bar` | A summary of trading over a time period (OHLCV) | symbol, open, high, low, close, volume, timestamp |
| `Trade` | A single transaction | symbol, price, size, timestamp, exchange |
| `OrderBook` | All current buy/sell orders at different prices | symbol, bids, asks, timestamp |
| `Exchange` | A marketplace where trading happens | name, mic, country, timezone, hours |
| `Symbol` | A traded asset (stock, ETF, crypto) | ticker, name, type, tick_size |
