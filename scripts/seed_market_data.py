"""Seed local database with reproducible multi-regime market candles for backtesting & local testing."""

from __future__ import annotations

import asyncio
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.backtesting.historical_data import HistoricalDataLoader  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.database.repositories.trading_repo import TradingRepository  # noqa: E402
from app.database.session import get_db_manager  # noqa: E402


async def main() -> None:
    settings = get_settings()
    db = get_db_manager()
    await db.init_models()
    async with db.session() as session:
        repo = TradingRepository(session)
        for idx, symbol in enumerate(settings.symbols):
            candles = HistoricalDataLoader.generate_synthetic_regime_series(
                bars=300,
                start_price=65000.0 if "BTC" in symbol else 3200.0,
                seed=42 + idx,
            )
            inserted = await repo.save_candles(
                exchange=settings.exchange_id,
                symbol=symbol,
                timeframe="15m",
                candles=candles,
            )
            print(f"Seeded {inserted} candles for {symbol} (15m).")


if __name__ == "__main__":
    asyncio.run(main())
