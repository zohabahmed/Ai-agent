"""Adaptive Worker: periodically evaluates strategy drift and runs walk-forward adaptation."""

from __future__ import annotations

import asyncio

from app.adaptive.adaptation_manager import AdaptationManager
from app.backtesting.performance_metrics import TradeRecord
from app.config import get_settings
from app.database.repositories.trading_repo import TradingRepository
from app.database.session import get_db_manager
from app.market.candle_manager import CandleManager
from app.strategies.strategy_registry import StrategyRegistry


class AdaptiveWorker:
    """Evaluates live/paper strategy drift and validates replacements on schedule."""

    def __init__(self, registry: StrategyRegistry | None = None) -> None:
        self.settings = get_settings()
        self.registry = registry or StrategyRegistry()
        self.manager = AdaptationManager(registry=self.registry)
        self._running = False

    async def run_once(self, symbol: str = "BTC/USDT", timeframe: str = "15m") -> list[dict[str, object]]:
        db = get_db_manager()
        outcomes: list[dict[str, object]] = []
        async with db.session() as session:
            repo = TradingRepository(session)
            account = await repo.get_or_create_default_account()
            candles = await repo.get_candles(self.settings.exchange_id, symbol, timeframe, limit=400)
            df = CandleManager.to_dataframe(candles)
            if len(df) < 120:
                return outcomes

            trades = await repo.get_recent_trades(account.id, limit=200)
            for strat in self.registry.list_enabled():
                strat_trades = [
                    TradeRecord(
                        pnl=float(t.realized_pnl or 0),
                        return_pct=float((t.realized_pnl or 0) / max(1e-9, float(t.notional))),
                        fees=float(t.fee),
                        duration_bars=1,
                        regime=t.market_regime,
                    )
                    for t in trades
                    if t.strategy_name == strat.name and t.realized_pnl is not None
                ]
                out = await self.manager.evaluate_and_adapt(
                    session=session,
                    active_strategy_name=strat.name,
                    recent_trades=strat_trades,
                    historical_df=df,
                    symbol=symbol,
                    current_regime="MIXED",
                )
                outcomes.append(
                    {
                        "strategy": strat.name,
                        "deteriorated": out.deterioration_detected,
                        "switched": out.switched,
                        "activated": out.activated_strategy,
                    }
                )
        return outcomes

    async def run_forever(self, interval_seconds: int = 300) -> None:
        self._running = True
        while self._running:
            await self.run_once()
            await asyncio.sleep(interval_seconds)

    def stop(self) -> None:
        self._running = False


if __name__ == "__main__":
    asyncio.run(AdaptiveWorker().run_forever())
