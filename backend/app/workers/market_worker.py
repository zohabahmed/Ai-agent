"""Market Data Worker: collects and validates OHLCV + tickers + order books continuously."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from app.config import get_settings
from app.database.repositories.trading_repo import TradingRepository
from app.database.session import get_db_manager
from app.exchanges.exchange_factory import ExchangeFactory
from app.market.data_collector import MarketDataCollector
from app.market.market_cache import MarketCache


class MarketWorker:
    """Periodically collects and validates market data for the configured universe."""

    def __init__(self, collector: MarketDataCollector | None = None) -> None:
        self.settings = get_settings()
        self.exchange = ExchangeFactory.create(self.settings)
        self.cache = MarketCache()
        self.collector = collector or MarketDataCollector(self.exchange, cache=self.cache, settings=self.settings)
        self._running = False

    async def run_once(self) -> dict[str, bool]:
        db = get_db_manager()
        results: dict[str, bool] = {}
        async with db.session() as session:
            repo = TradingRepository(session)
            for symbol in self.settings.symbols:
                for tf in self.settings.timeframe_list:
                    try:
                        snap = await self.collector.collect_symbol_data(session, symbol, timeframe=tf, limit=200)
                        results[f"{symbol}:{tf}"] = snap.is_tradeable
                    except Exception as exc:
                        results[f"{symbol}:{tf}"] = False
                        await repo.log_agent_event(
                            event_type="MARKET_DATA_ERROR",
                            component="market_worker",
                            severity="WARNING",
                            message=f"Failed to collect {symbol} {tf}: {type(exc).__name__}",
                            payload={"symbol": symbol, "timeframe": tf},
                        )
            await self.cache.set_json(
                "worker:market:heartbeat",
                {"timestamp": datetime.now(timezone.utc).isoformat(), "results": results},
                ttl_seconds=180,
            )
        return results

    async def run_forever(self, interval_seconds: int = 30) -> None:
        self._running = True
        try:
            while self._running:
                await self.run_once()
                await asyncio.sleep(interval_seconds)
        finally:
            await self.exchange.close()
            await self.cache.close()

    def stop(self) -> None:
        self._running = False


if __name__ == "__main__":
    worker = MarketWorker()
    asyncio.run(worker.run_forever())
