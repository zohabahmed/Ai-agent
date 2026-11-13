"""Market data collector coordinating exchange retrieval, validation, DB storage, and Redis caching."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.database.repositories.trading_repo import TradingRepository
from app.exchanges.base_exchange import BaseExchange, MarketRules, OrderBook, OrderBookLevel, Ticker
from app.market.candle_manager import CandleManager
from app.market.data_validator import MarketDataValidator, ValidationReport
from app.market.market_cache import MarketCache
from app.market.orderbook_manager import LiquidityMetrics, OrderBookManager


@dataclass
class MarketSnapshot:
    symbol: str
    timeframe: str
    ticker: Ticker
    rules: MarketRules
    liquidity: LiquidityMetrics
    candles_df: Any
    validation: ValidationReport
    ticker_valid: bool
    validation_errors: list[str]
    collected_at: datetime

    @property
    def is_tradeable(self) -> bool:
        return self.ticker_valid and self.validation.is_valid and self.rules.is_active


class MarketDataCollector:
    """Collects, validates, persists, and caches market data."""

    def __init__(
        self,
        exchange: BaseExchange,
        cache: MarketCache | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.exchange = exchange
        self.settings = settings or get_settings()
        self.cache = cache or MarketCache()
        self.validator = MarketDataValidator(
            stale_max_seconds=self.settings.stale_data_max_seconds,
            max_spread_bps=self.settings.max_spread_bps,
        )

    async def fetch_raw_bundle(
        self,
        symbol: str,
        timeframe: str = "15m",
        limit: int = 200,
        *,
        cached_ticker: Ticker | None = None,
        skip_order_book: bool = False,
    ) -> tuple[MarketRules, Ticker, OrderBook, list[dict[str, Any]]]:
        """Fetch raw market rules, ticker, orderbook, and OHLCV (optionally reusing batch tickers for fast multi-chart scans)."""
        rules = await self.exchange.get_market_rules(symbol)
        ticker = cached_ticker if cached_ticker is not None else await self.exchange.fetch_ticker(symbol)
        if skip_order_book:
            mid = ticker.last if ticker.last > 0 else Decimal("1")
            bid_px = ticker.bid if ticker.bid > 0 else mid
            ask_px = ticker.ask if ticker.ask >= bid_px else mid
            qty_est = Decimal("1000") / mid if mid > 0 else Decimal("10")
            order_book = OrderBook(
                symbol=symbol,
                bids=[OrderBookLevel(price=bid_px, quantity=qty_est)],
                asks=[OrderBookLevel(price=ask_px, quantity=qty_est)],
                timestamp=ticker.timestamp,
            )
        else:
            order_book = await self.exchange.fetch_order_book(symbol, limit=20)
        raw_candles = await self.exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
        return rules, ticker, order_book, raw_candles

    async def persist_raw_bundle(
        self,
        session: AsyncSession,
        symbol: str,
        timeframe: str,
        rules: MarketRules,
        ticker: Ticker,
        order_book: OrderBook,
        raw_candles: list[dict[str, Any]],
        *,
        allow_historical: bool = False,
    ) -> MarketSnapshot:
        """Validate, persist to DB, cache ticker, and build MarketSnapshot sequentially."""
        repo = TradingRepository(session)
        report = self.validator.validate_candles(
            raw_candles, timeframe=timeframe, allow_historical=allow_historical
        )
        ticker_ok, ticker_errors = self.validator.validate_ticker(ticker)
        liquidity = OrderBookManager.analyze(order_book)

        if report.valid_candles:
            await repo.save_candles(
                exchange=self.exchange.exchange_id,
                symbol=symbol,
                timeframe=timeframe,
                candles=report.valid_candles,
            )

        await self.cache.set_json(
            f"market:ticker:{symbol}",
            {
                "symbol": symbol,
                "last": str(ticker.last),
                "bid": str(ticker.bid),
                "ask": str(ticker.ask),
                "spread_bps": str(ticker.spread_bps),
                "volume_24h": str(ticker.volume_24h),
                "timestamp": ticker.timestamp.isoformat(),
                "valid": ticker_ok,
            },
            ttl_seconds=self.settings.stale_data_max_seconds,
        )

        df = CandleManager.to_dataframe(report.valid_candles)
        all_errors = [*report.errors, *ticker_errors]
        return MarketSnapshot(
            symbol=symbol,
            timeframe=timeframe,
            ticker=ticker,
            rules=rules,
            liquidity=liquidity,
            candles_df=df,
            validation=report,
            ticker_valid=ticker_ok,
            validation_errors=all_errors,
            collected_at=datetime.now(timezone.utc),
        )

    async def _collect_exact(
        self,
        session: AsyncSession,
        symbol: str,
        timeframe: str = "15m",
        limit: int = 200,
        *,
        allow_historical: bool = False,
    ) -> MarketSnapshot:
        """Collect market data for one exact symbol on this exchange without fallback recursion."""
        rules, ticker, order_book, raw_candles = await self.fetch_raw_bundle(
            symbol, timeframe=timeframe, limit=limit
        )
        return await self.persist_raw_bundle(
            session,
            symbol,
            timeframe,
            rules,
            ticker,
            order_book,
            raw_candles,
            allow_historical=allow_historical,
        )

    async def collect_symbol_data(
        self,
        session: AsyncSession,
        symbol: str,
        timeframe: str = "15m",
        limit: int = 200,
        *,
        allow_historical: bool = False,
    ) -> MarketSnapshot:
        try:
            return await self._collect_exact(
                session,
                symbol,
                timeframe=timeframe,
                limit=limit,
                allow_historical=allow_historical,
            )
        except Exception:
            from app.exchanges.ccxt_adapter import CCXTExchangeAdapter
            from app.market.universe_discovery import GlobalMarketDiscoveryService

            if not isinstance(self.exchange, CCXTExchangeAdapter):
                raise
            snap, _ = await GlobalMarketDiscoveryService.collect_symbol_snapshot_any(
                self,
                session,
                symbol,
                timeframe=timeframe,
                limit=limit,
                allow_historical=allow_historical,
                allow_external_feed=getattr(self.exchange, "_read_only", True),
            )
            return snap
