"""Shared pytest fixtures for unit, integration, backtesting, security, and failure-injection tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
import uuid

import pytest
import pytest_asyncio

from app.backtesting.historical_data import HistoricalDataLoader
from app.database.session import DatabaseManager, reset_db_manager
from app.exchanges.base_exchange import (
    BaseExchange,
    ExchangeBalance,
    ExchangeError,
    MarketRules,
    OrderBook,
    OrderBookLevel,
    OrderRequest,
    OrderResult,
    Ticker,
)


class MockExchange(BaseExchange):
    """Deterministic in-memory mock exchange for integration and failure-injection testing."""

    def __init__(self, *, fail_ping: bool = False, fail_create_order: bool = False) -> None:
        self.exchange_id = "binance"
        self.fail_ping = fail_ping
        self.fail_create_order = fail_create_order
        self.candles_by_symbol: dict[str, list[dict[str, Any]]] = {}
        self.balances: dict[str, ExchangeBalance] = {
            "USDT": ExchangeBalance(asset="USDT", free=Decimal("100.0"), used=Decimal("0"), total=Decimal("100.0"))
        }
        self.submitted_orders: list[OrderRequest] = []
        self.canceled_orders: list[str] = []

    async def connect(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def ping(self) -> float:
        if self.fail_ping:
            raise ExchangeError("Simulated exchange API outage.")
        return 12.5

    async def get_market_rules(self, symbol: str) -> MarketRules:
        base, quote = symbol.split("/")
        return MarketRules(
            symbol=symbol,
            base_asset=base,
            quote_asset=quote,
            min_quantity=Decimal("0.0001"),
            min_notional=Decimal("5.0"),
            quantity_precision=6,
            price_precision=2,
            maker_fee=Decimal("0.001"),
            taker_fee=Decimal("0.001"),
            is_active=True,
        )

    async def fetch_ohlcv(
        self, symbol: str, timeframe: str, since: datetime | None = None, limit: int = 200
    ) -> list[dict[str, Any]]:
        if symbol not in self.candles_by_symbol:
            raw = HistoricalDataLoader.generate_synthetic_regime_series(bars=limit, seed=42)
            # Align latest candle near now for freshness validation
            now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
            step = timedelta(minutes=15)
            for idx, c in enumerate(raw):
                c["timestamp"] = now - step * (len(raw) - 1 - idx)
            self.candles_by_symbol[symbol] = raw
        return self.candles_by_symbol[symbol][-limit:]

    async def fetch_ticker(self, symbol: str) -> Ticker:
        candles = await self.fetch_ohlcv(symbol, "15m", limit=10)
        last = candles[-1]["close"]
        bid = (last * Decimal("0.9998")).quantize(Decimal("0.01"))
        ask = (last * Decimal("1.0002")).quantize(Decimal("0.01"))
        return Ticker(
            symbol=symbol,
            last=last,
            bid=bid,
            ask=ask,
            volume_24h=Decimal("25000000"),
            timestamp=datetime.now(timezone.utc),
        )

    async def fetch_order_book(self, symbol: str, limit: int = 20) -> OrderBook:
        t = await self.fetch_ticker(symbol)
        bids = [OrderBookLevel(price=t.bid - Decimal(i), quantity=Decimal("1.5")) for i in range(5)]
        asks = [OrderBookLevel(price=t.ask + Decimal(i), quantity=Decimal("1.5")) for i in range(5)]
        return OrderBook(symbol=symbol, bids=bids, asks=asks, timestamp=datetime.now(timezone.utc))

    async def fetch_balances(self) -> dict[str, ExchangeBalance]:
        return self.balances

    async def create_order(self, request: OrderRequest) -> OrderResult:
        if self.fail_create_order:
            raise ExchangeError("Simulated ambiguous network timeout during order submission.")
        self.submitted_orders.append(request)
        return OrderResult(
            exchange_order_id=f"mock-{uuid.uuid4().hex[:10]}",
            client_order_id=request.client_order_id,
            symbol=request.symbol,
            side=request.side,
            order_type=request.order_type,
            status="FILLED",
            requested_quantity=request.quantity,
            filled_quantity=request.quantity,
            average_price=request.price or Decimal("65000"),
            fee=Decimal("0.01"),
            fee_asset="USDT",
            timestamp=datetime.now(timezone.utc),
        )

    async def cancel_order(self, exchange_order_id: str, symbol: str) -> OrderResult:
        self.canceled_orders.append(exchange_order_id)
        return OrderResult(
            exchange_order_id=exchange_order_id,
            client_order_id=exchange_order_id,
            symbol=symbol,
            side="BUY",
            order_type="LIMIT",
            status="CANCELED",
            requested_quantity=Decimal("1"),
            filled_quantity=Decimal("0"),
            average_price=None,
            fee=Decimal("0"),
            fee_asset="USDT",
            timestamp=datetime.now(timezone.utc),
        )

    async def fetch_order(self, exchange_order_id: str, symbol: str) -> OrderResult:
        return OrderResult(
            exchange_order_id=exchange_order_id,
            client_order_id=exchange_order_id,
            symbol=symbol,
            side="BUY",
            order_type="MARKET",
            status="FILLED",
            requested_quantity=Decimal("0.001"),
            filled_quantity=Decimal("0.001"),
            average_price=Decimal("65000"),
            fee=Decimal("0.065"),
            fee_asset="USDT",
            timestamp=datetime.now(timezone.utc),
        )

    async def fetch_my_trades(self, symbol: str, limit: int = 50) -> list[dict[str, Any]]:
        return []


@pytest_asyncio.fixture
async def db_manager(tmp_path: Path) -> DatabaseManager:
    db_file = tmp_path / "test_survival.db"
    url = f"sqlite+aiosqlite:///{db_file.as_posix()}"
    mgr = reset_db_manager(url)
    await mgr.init_models()
    yield mgr
    await mgr.dispose()


@pytest.fixture
def mock_exchange() -> MockExchange:
    return MockExchange()
