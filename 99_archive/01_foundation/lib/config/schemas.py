from typing import Optional

from pydantic import BaseModel, Field


class MarketDataSource(BaseModel):
    name: str = "polygon"
    api_key: str = ""
    base_url: str = "https://api.polygon.io"
    rate_limit: int = 5
    timeout: int = 30


class BrokerConfig(BaseModel):
    name: str = "alpaca"
    api_key: str = ""
    secret_key: str = ""
    base_url: str = "https://paper-api.alpaca.markets"
    paper: bool = True


class StrategyConfig(BaseModel):
    name: str = "momentum"
    enabled: bool = True
    params: dict = Field(default_factory=dict)


class MarketConfig(BaseModel):
    symbols: list[str] = Field(default_factory=lambda: ["SPY", "QQQ"])
    data_sources: list[MarketDataSource] = Field(default_factory=lambda: [MarketDataSource()])
    default_bar_size: str = "1d"
    max_lookback_days: int = 365


class LoggingConfig(BaseModel):
    level: str = "INFO"
    format: str = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    file: Optional[str] = None


class AppConfig(BaseModel):
    app_name: str = "vayren-core"
    environment: str = "development"
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    market: MarketConfig = Field(default_factory=MarketConfig)
    strategies: list[StrategyConfig] = Field(default_factory=lambda: [StrategyConfig()])
    brokers: list[BrokerConfig] = Field(default_factory=lambda: [BrokerConfig()])
