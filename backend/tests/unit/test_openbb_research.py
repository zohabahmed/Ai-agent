"""Unit tests for OpenBB ODP Quantitative Research & Confluence Engine."""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.intelligence.openbb_research import OpenBBQuantResearch


def _make_candles(prices: np.ndarray, volumes: np.ndarray | None = None) -> pd.DataFrame:
    n = len(prices)
    idx = pd.date_range("2025-01-01", periods=n, freq="15min", tz="UTC")
    vols = volumes if volumes is not None else np.full(n, 250.0)
    return pd.DataFrame(
        {
            "open": prices * 0.999,
            "high": prices * 1.003,
            "low": prices * 0.997,
            "close": prices,
            "volume": vols,
        },
        index=idx,
    )


def test_openbb_identifies_bullish_uptrend_confluence():
    prices = np.linspace(100.0, 145.0, 90)
    volumes = np.linspace(100.0, 600.0, 90)
    df = _make_candles(prices, volumes)

    report = OpenBBQuantResearch().analyze(df)
    assert report.openbb_enabled is True
    assert report.supertrend_bullish is True
    assert report.obv_accumulation is True
    assert report.confluence_score >= 0.60
    allowed, _ = report.allows_strategy_entry("trend_following")
    assert allowed is True

    obb = report.to_obbject()
    assert obb.provider == OpenBBQuantResearch.PROVIDER_LABEL


def test_openbb_filters_bearish_downtrend_trend_entry():
    prices = np.linspace(145.0, 95.0, 90)
    volumes = np.linspace(600.0, 100.0, 90)
    df = _make_candles(prices, volumes)

    report = OpenBBQuantResearch().analyze(df)
    assert report.supertrend_bullish is False
    assert report.confluence_score <= 0.40
    allowed, reason = report.allows_strategy_entry("trend_following")
    assert allowed is False
    assert "OpenBB" in reason


def test_openbb_handles_insufficient_candles_safely():
    df = _make_candles(np.linspace(100.0, 102.0, 15))
    report = OpenBBQuantResearch().analyze(df)
    assert report.quality_gate_passed is False
    assert report.trend_bias == "NEUTRAL"
