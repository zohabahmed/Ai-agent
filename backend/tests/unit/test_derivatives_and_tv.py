"""Tests for Real-Time Derivatives (CoinGlass/Binance Futures) & TradingView Technical Rating Engine."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from app.intelligence.derivatives_intelligence import DerivativesIntelligenceService


def _make_sample_df(rows: int = 80, trend: float = 0.002) -> pd.DataFrame:
    rng = np.random.default_rng(42)
    prices = [85000.0]
    for _ in range(rows - 1):
        step = trend + rng.normal(0.0, 0.0025)
        prices.append(prices[-1] * (1.0 + step))
    closes = np.array(prices)
    highs = closes * 1.003
    lows = closes * 0.997
    opens = np.roll(closes, 1)
    opens[0] = closes[0]
    volumes = rng.uniform(50.0, 250.0, size=rows)
    return pd.DataFrame(
        {
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": volumes,
        }
    )


def test_tradingview_rating_bullish_and_bearish() -> None:
    svc = DerivativesIntelligenceService()
    bull_df = _make_sample_df(rows=100, trend=0.004)
    bull_report = svc.compute_tradingview_rating(bull_df)
    assert bull_report.overall_rating in {"STRONG_BUY", "BUY"}
    assert bull_report.rating_score > 0.15
    assert bull_report.ma_buy_count >= 4
    assert "EMA_20" in bull_report.signals
    assert "RSI_14" in bull_report.signals

    bear_df = _make_sample_df(rows=100, trend=-0.004)
    bear_report = svc.compute_tradingview_rating(bear_df)
    assert bear_report.overall_rating in {"STRONG_SELL", "SELL"}
    assert bear_report.rating_score < -0.15
    assert bear_report.ma_sell_count >= 4


def test_enrich_candles_for_chart() -> None:
    svc = DerivativesIntelligenceService()
    base_time = datetime(2026, 1, 1, tzinfo=timezone.utc)
    candles = [
        SimpleNamespace(
            timestamp=base_time + timedelta(minutes=15 * i),
            open=84000.0 + i * 25.0,
            high=84100.0 + i * 25.0,
            low=83900.0 + i * 25.0,
            close=84050.0 + i * 25.0,
            volume=120.0 + i,
        )
        for i in range(40)
    ]
    enriched = svc.enrich_candles_for_chart(candles)
    assert len(enriched) == 40
    last = enriched[-1]
    assert "ema_20" in last
    assert "ema_50" in last
    assert "bb_upper" in last
    assert "bb_lower" in last
    assert "rsi_14" in last
    assert last["bb_upper"] >= last["bb_lower"]


@pytest.mark.asyncio
async def test_fetch_derivatives_sentiment_with_fallback() -> None:
    svc = DerivativesIntelligenceService(timeout_seconds=1.5)
    df = _make_sample_df(rows=60, trend=0.002)
    report = await svc.fetch_derivatives_sentiment("BTC/USDT", df)
    assert report.symbol == "BTC/USDT"
    assert report.crowding_state in {
        "BALANCED",
        "CROWDED_LONG",
        "CROWDED_SHORT",
        "BULLISH_FLOW",
        "BEARISH_FLOW",
    }
    assert 0.0 <= report.long_account_pct <= 100.0
    assert 0.0 <= report.short_account_pct <= 100.0
    assert isinstance(report.allow_long, bool)
    assert isinstance(report.allow_short, bool)
