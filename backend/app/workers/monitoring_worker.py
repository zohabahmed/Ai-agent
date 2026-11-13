"""Monitoring Worker: continuous health, balance reconciliation, and risk watchdog."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from app.config import TradingMode, get_settings
from app.database.repositories.trading_repo import TradingRepository
from app.database.session import get_db_manager
from app.exchanges.exchange_factory import ExchangeFactory
from app.exchanges.exchange_health import ExchangeHealthMonitor
from app.execution.reconciliation import ReconciliationService
from app.market.market_cache import MarketCache


class MonitoringWorker:
    """Monitors database, exchange connectivity, and reconciliation integrity."""

    def __init__(self) -> None:
        self.settings = get_settings()
        self.exchange = ExchangeFactory.create(self.settings)
        self.health_monitor = ExchangeHealthMonitor()
        self.reconciliation = ReconciliationService()
        self.cache = MarketCache()
        self._running = False

    async def run_once(self) -> dict[str, object]:
        db = get_db_manager()
        db_ok = await db.health_check()
        ex_status = await self.health_monitor.check(self.exchange)
        recon_ok = False
        if db_ok:
            async with db.session() as session:
                repo = TradingRepository(session)
                account = await repo.get_or_create_default_account(
                    exchange=self.exchange.exchange_id,
                    trading_mode=self.settings.trading_mode.value,
                    initial_capital=self.settings.initial_capital_usd,
                    quote_currency=self.settings.quote_currency,
                )
                rep = await self.reconciliation.reconcile(
                    session=session,
                    account_id=account.id,
                    exchange=self.exchange,
                    trading_mode=TradingMode(account.trading_mode),
                )
                recon_ok = rep.verified

        status = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "db_healthy": db_ok,
            "exchange_healthy": ex_status.is_healthy,
            "exchange_latency_ms": ex_status.latency_ms,
            "reconciliation_ok": recon_ok,
        }
        await self.cache.set_json("worker:monitoring:heartbeat", status, ttl_seconds=180)
        return status

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
    asyncio.run(MonitoringWorker().run_forever())
