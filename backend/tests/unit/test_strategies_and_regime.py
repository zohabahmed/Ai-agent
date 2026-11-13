"""Unit tests for all 4 strategies and Market Regime Detector."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from app.intelligence.regime_detector import MarketRegime, MarketRegimeDetector
from app.strategies.mean_reversion import MeanReversionStrategy
from app.strategies.momentum import MomentumStrategy
from app.strategies.strategy_registry import StrategyRegistry
from app.strategies.trend_following import TrendFollowingStrategy
from app.strategies.volatility_strategy import VolatilityStrategy


def _make_df(closes: list[float], volumes: list[float] | None = None) -> pd.DataFrame:
    n = len(closes)
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    vols = volumes if volumes is not None else [150.0] * n
    rows = []
    for i, c in enumerate(closes):
        o = closes[i - 1] if i > 0 else c
        h = max(o, c) * 1.002
        l = min(o, c) * 0.998
        rows.append(
            {
                "timestamp": start + timedelta(minutes=15 * i),
                "open": o,
                "high": h,
                "low": l,
                "close": c,
                "volume": vols[i],
            }
        )
    return pd.DataFrame(rows)


def test_trend_following_and_momentum_detect_bullish_trend():
    # Consolidation -> steady uptrend -> mild pullback -> volume breakout
    closes = [100.0 + float(np.sin(i / 3.0) * 0.35) for i in range(55)]
    p = closes[-1]
    for i in range(25):
        p = p * (1.0 + (-0.0025 if i % 3 == 1 else 0.0032))
        closes.append(p)
    for step in [-0.0035, -0.0035, -0.0030, -0.0025, -0.0015]:
        p = p * (1.0 + step)
        closes.append(p)
    for step in [0.0022, 0.0025, 0.0028, 0.0032, 0.0035, 0.0038]:
        p = p * (1.0 + step)
        closes.append(p)
    vols = [100.0] * (len(closes) - 5) + [260.0] * 5
    df = _make_df(closes, vols)

    regime = MarketRegimeDetector().detect(df)
    assert regime.primary_regime == MarketRegime.BULLISH_TRENDING

    tf_sig = TrendFollowingStrategy().generate_signal("BTC/USDT", df)
    mom_sig = MomentumStrategy().generate_signal("BTC/USDT", df)
    assert tf_sig.action == "BUY"
    assert tf_sig.stop_loss < tf_sig.entry_price < tf_sig.take_profit
    assert mom_sig.action == "BUY"


def test_regime_detector_classifies_sideways_and_abnormal_shock():
    rng = np.random.default_rng(7)
    sideways = [100.0 + float(np.sin(i / 3.0) * 0.25 + rng.normal(0, 0.05)) for i in range(90)]
    df_side = _make_df(sideways)
    reg_side = MarketRegimeDetector().detect(df_side)
    assert reg_side.primary_regime in {
        MarketRegime.SIDEWAYS,
        MarketRegime.LOW_VOLATILITY,
        MarketRegime.VOLATILITY_CONTRACTION,
    }

    # Inject a 15% flash crash on the latest bar
    shocked = sideways[:-1] + [sideways[-2] * 0.85]
    df_shock = _make_df(shocked)
    reg_shock = MarketRegimeDetector().detect(df_shock)
    assert reg_shock.primary_regime == MarketRegime.ABNORMAL_UNSTABLE
    assert reg_shock.is_abnormal is True


def test_mean_reversion_and_volatility_strategy_contracts():
    # Mean reversion oversold bounce
    base = [100.0 + np.sin(i / 4.0) * 0.5 for i in range(75)]
    drop = [99.0, 98.0, 96.5, 95.0, 95.4]  # Oversold + stabilization tick up
    df_mr = _make_df(base + drop)
    mr_sig = MeanReversionStrategy().generate_signal("ETH/USDT", df_mr)
    assert mr_sig.strategy == "mean_reversion"
    assert mr_sig.action in {"BUY", "HOLD"}
    assert "zscore" in mr_sig.indicators

    vol_sig = VolatilityStrategy().generate_signal("BTC/USDT", df_mr)
    assert vol_sig.strategy == "volatility_breakout"
    assert StrategyRegistry.available_names() == [
        "trend_following",
        "mean_reversion",
        "momentum",
        "volatility_breakout",
    ]
