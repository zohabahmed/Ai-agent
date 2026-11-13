"""Unit tests for Bayesian probability estimation and cost-adjusted Expected Value."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from app.exchanges.base_exchange import MarketRules, Ticker
from app.intelligence.confidence_estimator import ConfidenceEstimator
from app.intelligence.opportunity_engine import OpportunityEngine
from app.strategies.base_strategy import StrategySignal


def test_confidence_estimator_enforces_min_sample_and_conservative_bound():
    est = ConfidenceEstimator(min_sample_size=20)
    small = est.estimate(wins=4, losses=1)
    assert small.meets_min_sample is False
    assert small.conservative_win_probability < small.mean_win_probability

    large = est.estimate(wins=30, losses=15, oos_wins=10, oos_losses=5)
    assert large.meets_min_sample is True
    assert large.oos_validated is True
    assert large.ci_low < large.conservative_win_probability < large.mean_win_probability < large.ci_high


def test_expected_value_deducts_fees_spread_and_slippage():
    est = ConfidenceEstimator(min_sample_size=15)
    engine = OpportunityEngine(est)
    prob = est.estimate(wins=18, losses=7, oos_wins=6, oos_losses=2)
    rules = MarketRules(
        symbol="BTC/USDT",
        base_asset="BTC",
        quote_asset="USDT",
        min_quantity=Decimal("0.0001"),
        min_notional=Decimal("5"),
        quantity_precision=5,
        price_precision=2,
        maker_fee=Decimal("0.001"),
        taker_fee=Decimal("0.001"),
    )
    ticker = Ticker(
        symbol="BTC/USDT",
        last=Decimal("65000"),
        bid=Decimal("64990"),
        ask=Decimal("65010"),
        volume_24h=Decimal("1000000"),
        timestamp=datetime.now(timezone.utc),
    )
    # 2:1 RR with ~2% stop, ~4% target => positive EV even after ~20-30 bps costs
    good_sig = StrategySignal(
        strategy="trend_following",
        symbol="BTC/USDT",
        action="BUY",
        confidence=0.70,
        entry_price=Decimal("65000"),
        stop_loss=Decimal("63700"),
        take_profit=Decimal("67600"),
        reason="Test",
    )
    ev = engine.calculate_expected_value(signal=good_sig, prob=prob, rules=rules, ticker=ticker)
    assert ev.total_cost_pct > Decimal("0.002")
    assert ev.net_ev_pct < ev.gross_ev_pct
    assert ev.is_positive_ev is True

    # Tiny scalp target consumed by fees + spread + slippage => rejected
    scalp_sig = StrategySignal(
        strategy="trend_following",
        symbol="BTC/USDT",
        action="BUY",
        confidence=0.70,
        entry_price=Decimal("65000"),
        stop_loss=Decimal("64900"),
        take_profit=Decimal("65100"),
        reason="Tiny scalp",
    )
    bad_ev = engine.calculate_expected_value(signal=scalp_sig, prob=prob, rules=rules, ticker=ticker)
    assert bad_ev.is_positive_ev is False
