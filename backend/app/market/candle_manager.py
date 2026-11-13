"""OHLCV candle management and DataFrame conversion."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd

from app.database.models import MarketCandle


class CandleManager:
    """Converts candle records into validated Pandas DataFrames for quantitative strategies."""

    @staticmethod
    def to_dataframe(candles: list[dict[str, Any]] | list[MarketCandle]) -> pd.DataFrame:
        if not candles:
            return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])

        rows: list[dict[str, Any]] = []
        for c in candles:
            if isinstance(c, dict):
                rows.append(
                    {
                        "timestamp": c["timestamp"],
                        "open": float(c["open"]),
                        "high": float(c["high"]),
                        "low": float(c["low"]),
                        "close": float(c["close"]),
                        "volume": float(c["volume"]),
                    }
                )
            else:
                rows.append(
                    {
                        "timestamp": c.timestamp,
                        "open": float(c.open),
                        "high": float(c.high),
                        "low": float(c.low),
                        "close": float(c.close),
                        "volume": float(c.volume),
                    }
                )
        df = pd.DataFrame(rows)
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        df = df.drop_duplicates(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
        return df

    @staticmethod
    def latest_timestamp(df: pd.DataFrame) -> datetime | None:
        if df.empty:
            return None
        return df["timestamp"].iloc[-1].to_pydatetime()
