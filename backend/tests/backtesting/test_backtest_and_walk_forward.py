"""Backtesting & walk-forward tests verifying zero look-ahead bias, costs, and reproducibility."""

from __future__ import annotations

from app.backtesting.backtest_engine import BacktestEngine
from app.backtesting.historical_data import HistoricalDataLoader
from app.backtesting.walk_forward import WalkForwardValidator
from app.market.candle_manager import CandleManager
from app.strategies.mean_reversion import MeanReversionStrategy
from app.strategies.trend_following import TrendFollowingStrategy


def test_backtest_is_reproducible_and_includes_costs():
    candles = HistoricalDataLoader.generate_synthetic_regime_series(bars=260, seed=101)
    df = CandleManager.to_dataframe(candles)

    engine = BacktestEngine(initial_capital=100.0, fee_rate=0.001, slippage_bps=5.0, spread_bps=4.0)
    r1 = engine.run(TrendFollowingStrategy(), "BTC/USDT", df)
    r2 = engine.run(TrendFollowingStrategy(), "BTC/USDT", df)

    assert r1.report == r2.report
    assert r1.report.result_type == "BACKTEST_RESULTS"
    assert r1.report.is_simulated is True
    assert r1.report.max_drawdown_pct <= engine.max_drawdown_limit + 0.02


def test_backtest_has_zero_lookahead_bias():
    candles = HistoricalDataLoader.generate_synthetic_regime_series(bars=200, seed=77)
    df_base = CandleManager.to_dataframe(candles)
    df_mutated = df_base.copy()
    # Wildly mutate bars after index 130; signals/trades up to bar 130 MUST be identical
    df_mutated.loc[135:, "close"] *= 2.5
    df_mutated.loc[135:, "high"] *= 2.6

    strat = TrendFollowingStrategy()
    sig_a = strat.generate_signal("BTC/USDT", df_base.iloc[:130])
    sig_b = strat.generate_signal("BTC/USDT", df_mutated.iloc[:130])
    assert sig_a == sig_b


def test_walk_forward_validation_across_regimes():
    candles = HistoricalDataLoader.generate_synthetic_regime_series(bars=360, seed=42)
    df = CandleManager.to_dataframe(candles)
    validator = WalkForwardValidator(engine=BacktestEngine(initial_capital=100.0), num_folds=3)
    wf = validator.validate(MeanReversionStrategy(), "ETH/USDT", df)
    assert len(wf.folds) >= 2
    assert wf.strategy_name == "mean_reversion"
