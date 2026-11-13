"""Exchange-independent abstract adapter and normalized data structures."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any


@dataclass(frozen=True)
class MarketRules:
    symbol: str
    base_asset: str
    quote_asset: str
    min_quantity: Decimal
    min_notional: Decimal
    quantity_precision: int
    price_precision: int
    maker_fee: Decimal
    taker_fee: Decimal
    is_active: bool = True
    contract_size: Decimal = Decimal("1")
    lot_step: Decimal | None = None

    def for_paper_micro_trading(self, min_notional: Decimal = Decimal("0.25")) -> MarketRules:
        """Relax exchange lot-size floors only inside simulated PAPER mode so $1+ accounts can trade any coin."""
        return MarketRules(
            symbol=self.symbol,
            base_asset=self.base_asset,
            quote_asset=self.quote_asset,
            min_quantity=min(self.min_quantity, Decimal("0.0000001")),
            min_notional=min(self.min_notional, min_notional),
            quantity_precision=max(self.quantity_precision, 8),
            price_precision=self.price_precision,
            maker_fee=self.maker_fee,
            taker_fee=self.taker_fee,
            is_active=self.is_active,
            contract_size=Decimal("1"),
            lot_step=None,
        )


@dataclass(frozen=True)
class Ticker:
    symbol: str
    last: Decimal
    bid: Decimal
    ask: Decimal
    volume_24h: Decimal
    timestamp: datetime

    @property
    def mid(self) -> Decimal:
        if self.bid > 0 and self.ask > 0:
            return (self.bid + self.ask) / Decimal("2")
        return self.last

    @property
    def spread_bps(self) -> Decimal:
        mid = self.mid
        if mid <= 0 or self.ask <= 0 or self.bid <= 0:
            return Decimal("0")
        return ((self.ask - self.bid) / mid) * Decimal("10000")


@dataclass(frozen=True)
class OrderBookLevel:
    price: Decimal
    quantity: Decimal


@dataclass(frozen=True)
class OrderBook:
    symbol: str
    bids: list[OrderBookLevel]
    asks: list[OrderBookLevel]
    timestamp: datetime


@dataclass(frozen=True)
class ExchangeBalance:
    asset: str
    free: Decimal
    used: Decimal
    total: Decimal


@dataclass(frozen=True)
class OrderRequest:
    symbol: str
    side: str  # BUY / SELL
    order_type: str  # MARKET / LIMIT
    quantity: Decimal
    client_order_id: str
    price: Decimal | None = None
    reduce_only: bool = False


@dataclass(frozen=True)
class OrderResult:
    exchange_order_id: str
    client_order_id: str
    symbol: str
    side: str
    order_type: str
    status: str  # OPEN, PARTIAL, FILLED, CANCELED, REJECTED
    requested_quantity: Decimal
    filled_quantity: Decimal
    average_price: Decimal | None
    fee: Decimal
    fee_asset: str
    timestamp: datetime
    raw: dict[str, Any] = field(default_factory=dict)


class ExchangeError(Exception):
    """Sanitized exchange exception (never contains credentials)."""


class BaseExchange(ABC):
    """Abstract base interface for all spot exchanges."""

    exchange_id: str

    @abstractmethod
    async def connect(self) -> None:
        """Initialize connection and load markets."""

    @abstractmethod
    async def close(self) -> None:
        """Close underlying network resources."""

    @abstractmethod
    async def ping(self) -> float:
        """Check exchange connectivity and return latency in milliseconds."""

    @abstractmethod
    async def get_market_rules(self, symbol: str) -> MarketRules:
        """Retrieve trading rules, fees, precision, and minimum order sizes."""

    @abstractmethod
    async def fetch_ohlcv(
        self, symbol: str, timeframe: str, since: datetime | None = None, limit: int = 200
    ) -> list[dict[str, Any]]:
        """Fetch historical OHLCV candles."""

    @abstractmethod
    async def fetch_ticker(self, symbol: str) -> Ticker:
        """Fetch latest ticker quote."""

    @abstractmethod
    async def fetch_order_book(self, symbol: str, limit: int = 20) -> OrderBook:
        """Fetch order book depth."""

    @abstractmethod
    async def fetch_balances(self) -> dict[str, ExchangeBalance]:
        """Fetch spot balances."""

    @abstractmethod
    async def create_order(self, request: OrderRequest) -> OrderResult:
        """Submit a spot order."""

    @abstractmethod
    async def cancel_order(self, exchange_order_id: str, symbol: str) -> OrderResult:
        """Cancel an open order."""

    @abstractmethod
    async def fetch_order(self, exchange_order_id: str, symbol: str) -> OrderResult:
        """Fetch order status for reconciliation."""

    @abstractmethod
    async def fetch_my_trades(self, symbol: str, limit: int = 50) -> list[dict[str, Any]]:
        """Fetch recent account executions."""

    async def configure_futures_position(
        self, symbol: str, leverage: int, margin_mode: str = "isolated"
    ) -> None:
        """Configure isolated margin & leverage before Futures order entry (no-op for Spot)."""
        return None

    async def list_tradable_markets(
        self, quote: str = "USDT", query: str = "", limit: int = 100
    ) -> list[dict[str, Any]]:
        """List active tradable markets for global coin discovery (empty fallback for mocks)."""
        return []

    async def fetch_all_tickers(self, quote: str = "USDT") -> dict[str, Ticker]:
        """Batch-fetch tickers across active markets for global opportunity scanning."""
        return {}
