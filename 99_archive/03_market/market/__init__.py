"""Market Data Domain.

Provides market data ingestion, storage, and query capabilities.
Publishes BarReceived and TradeReceived events.

Public API:
    models: Bar, Trade, OrderBook, Exchange, Symbol
    services: MarketDataQuery
    events: BarReceived, TradeReceived
"""

from market.models.bar import Bar
from market.models.trade import Trade
from market.models.order_book import OrderBook
from market.models.exchange import Exchange
from market.models.symbol import Symbol
from market.services.query import MarketDataQuery
from market.events.bar_received import BarReceived
from market.events.trade_received import TradeReceived

__all__ = [
    "Bar",
    "Trade",
    "OrderBook",
    "Exchange",
    "Symbol",
    "MarketDataQuery",
    "BarReceived",
    "TradeReceived",
]
