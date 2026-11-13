"""Historical market data loader and deterministic multi-regime generator for testing."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


class HistoricalDataLoader:
    """Loads historical OHLCV from CSV or generates reproducible multi-regime test series."""

    @staticmethod
    def from_csv(path: str | Path) -> pd.DataFrame:
        df = pd.read_csv(path)
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        return df.sort_values("timestamp").reset_index(drop=True)

    @staticmethod
    def generate_synthetic_regime_series(
        *,
        bars: int = 360,
        start_price: float = 65000.0,
        timeframe_minutes: int = 15,
        seed: int = 42,
    ) -> list[dict[str, Any]]:
        """
        Generates a reproducible multi-regime OHLCV series:
        1) Sideways, 2) Bullish trend, 3) High-volatility pullback, 4) Recovery trend.
        """
        rng = np.random.default_rng(seed)
        start_ts = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
        price = start_price
        candles: list[dict[str, Any]] = []

        for i in range(bars):
            frac = i / max(1, bars)
            if frac < 0.25:
                # Sideways mean-reverting
                drift = 0.0002 * np.sin(i / 4.0)
                vol = 0.0025
            elif frac < 0.55:
                # Bullish trending
                drift = 0.0018
                vol = 0.0030
            elif frac < 0.75:
                # Bearish / volatile
                drift = -0.0014
                vol = 0.0065
            else:
                # Controlled bullish recovery
                drift = 0.0015
                vol = 0.0028

            ret = drift + float(rng.normal(0.0, vol))
            open_p = price
            close_p = max(1.0, open_p * (1.0 + ret))
            wick_up = abs(float(rng.normal(0.0, vol * 0.6)))
            wick_dn = abs(float(rng.normal(0.0, vol * 0.6)))
            high_p = max(open_p, close_p) * (1.0 + wick_up)
            low_p = min(open_p, close_p) * (1.0 - wick_dn)
            volume = float(100.0 + abs(ret) * 40000.0 + rng.uniform(10.0, 60.0))

            candles.append(
                {
                    "timestamp": start_ts + timedelta(minutes=timeframe_minutes * i),
                    "open": Decimal(str(round(open_p, 4))),
                    "high": Decimal(str(round(high_p, 4))),
                    "low": Decimal(str(round(low_p, 4))),
                    "close": Decimal(str(round(close_p, 4))),
                    "volume": Decimal(str(round(volume, 4))),
                }
            )
            price = close_p

        return candles
