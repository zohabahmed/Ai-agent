"""Unit & integration tests verifying all SurvivalX Code Review fixes (Findings 1-8, Phase 0-2)."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pandas as pd
import pytest

from app.backtesting.backtest_engine import BacktestEngine
from app.backtesting.historical_data import HistoricalDataLoader
from app.config import Settings, TradingMode
from app.exchanges.base_exchange import MarketRules, OrderBook, Ticker
from app.execution.exit_policy import evaluate_position_exit_step, normalize_atr_targets
from app.execution.protective_orders import (
    cancel_protective_orders,
    detect_triggered_protective_fill,
    place_protective_orders,
    sync_stop_loss_order,
)
from app.intelligence.confidence_estimator import ConfidenceEstimator
from app.intelligence.derivatives_intelligence import DerivativesSentimentReport
from app.intelligence.opportunity_engine import EvaluatedOpportunity, OpportunityEngine
from app.intelligence.regime_detector import MarketRegime, RegimeAssessment
from app.intelligence.strategy_validation_service import (
    check_phase2_entry_filters,
    evaluate_separated_oos_stats,
)
from app.market.candle_manager import CandleManager
from app.risk.daily_baseline import sync_utc_day_baseline
from app.risk.risk_manager import RiskManager
from app.strategies.base_strategy import StrategySignal
from app.strategies.trend_following import TrendFollowingStrategy


def _make_rules(symbol: str = "ETH/USDT") -> MarketRules:
    return MarketRules(
        symbol=symbol,
        base_asset=symbol.split("/")[0],
        quote_asset="USDT",
        min_quantity=Decimal("0.001"),
        min_notional=Decimal("5.0"),
        quantity_precision=3,
        price_precision=2,
        maker_fee=Decimal("0.0002"),
        taker_fee=Decimal("0.0005"),
        is_active=True,
    )


def _make_ticker(symbol: str = "ETH/USDT", px: Decimal = Decimal("3000.00")) -> Ticker:
    return Ticker(
        symbol=symbol,
        last=px,
        bid=px * Decimal("0.9999"),
        ask=px * Decimal("1.0001"),
        volume_24h=Decimal("25000000"),
        timestamp=datetime.now(timezone.utc),
    )


def test_utc_daily_baseline_rollover_and_profit_preservation() -> None:
    """Finding 4: UTC 00:00 day_start_equity snapshots daily so daily loss protects accumulated profits."""
    account = SimpleNamespace(
        initial_capital=Decimal("100.00"),
        day_start_equity=Decimal("100.00"),
        day_start_date="2026-11-12",
    )

    # Same UTC day -> keep existing daily baseline intact even if equity fluctuates
    same_day = datetime(2026, 11, 12, 18, 30, tzinfo=timezone.utc)
    rolled_same = sync_utc_day_baseline(account, Decimal("115.00"), now_utc=same_day)
    assert rolled_same is False
    assert account.day_start_equity == Decimal("100.00")

    # Next UTC day at 00:05 UTC -> snapshot accumulated $120 equity as today's baseline
    next_day = datetime(2026, 11, 13, 0, 5, tzinfo=timezone.utc)
    rolled_next = sync_utc_day_baseline(account, Decimal("120.00"), now_utc=next_day)
    assert rolled_next is True
    assert account.day_start_equity == Decimal("120.00")
    assert account.day_start_date == "2026-11-13"

    # Dropping -1.5% intraday from $120 -> $118.20 (still +18.2% above initial $100!) MUST breach daily loss
    settings = Settings(trading_mode=TradingMode.PAPER, max_daily_loss=Decimal("0.01"))
    rm = RiskManager(settings=settings)
    sig = StrategySignal(
        strategy="trend_following",
        symbol="ETH/USDT",
        action="BUY",
        confidence=0.8,
        entry_price=Decimal("3000.00"),
        stop_loss=Decimal("2964.00"),
        take_profit=Decimal("3090.00"),
        reason="Test signal",
    )
    regime = RegimeAssessment(
        primary_regime=MarketRegime.BULLISH_TRENDING,
        secondary_regimes=[],
        confidence=0.8,
        volatility_pct=0.015,
        trend_strength_adx=32.0,
        trend_slope=0.004,
        reason="Uptrend",
    )
    prob = ConfidenceEstimator().estimate(wins=40, losses=15)
    ev = OpportunityEngine().calculate_expected_value(signal=sig, prob=prob, rules=_make_rules(), ticker=_make_ticker())
    opp = EvaluatedOpportunity(signal=sig, regime=regime, probability=prob, ev=ev, regime_compatible=True, score=1.0)

    res = rm.evaluate_trade(
        opportunity=opp,
        account_equity=Decimal("118.20"),
        peak_equity=Decimal("120.00"),
        day_start_equity=account.day_start_equity,
        available_balance=Decimal("118.20"),
        open_positions_count=0,
        current_open_notional=Decimal("0"),
        rules=_make_rules(),
        db_healthy=True,
        exchange_healthy=True,
        market_data_valid=True,
        balance_verified=True,
        market_type="FUTURES",
        leverage=1,
    )
    assert res.approved is False
    assert res.drawdown.daily_loss_breached is True


@pytest.mark.asyncio
async def test_exchange_protective_orders_lifecycle(mock_exchange) -> None:
    """Finding 5: Exchange-side reduce-only STOP_MARKET & TAKE_PROFIT_MARKET creation, SL ratchet, trigger fill, and cleanup."""
    rules = _make_rules("BTC/USDT")
    sl_id, tp_id, warnings = await place_protective_orders(
        mock_exchange,
        symbol="BTC/USDT",
        position_side="LONG",
        quantity=Decimal("0.015"),
        stop_loss=Decimal("68000.00"),
        take_profit=Decimal("71500.00"),
        rules=rules,
        trading_mode=TradingMode.TESTNET,
        market_type="FUTURES",
    )
    assert warnings == []
    assert sl_id is not None and tp_id is not None
    sl_req = next(o for o in mock_exchange.submitted_orders if o.client_order_id.startswith("slprot"))
    tp_req = next(o for o in mock_exchange.submitted_orders if o.client_order_id.startswith("tpprot"))
    assert sl_req.order_type == "STOP_MARKET" and sl_req.reduce_only is True
    assert tp_req.order_type == "TAKE_PROFIT_MARKET" and tp_req.reduce_only is True

    # Ratchet SL up -> old SL order canceled & replacement created
    pos = SimpleNamespace(
        symbol="BTC/USDT",
        side="LONG",
        quantity=Decimal("0.015"),
        market_type="FUTURES",
        current_price=Decimal("70200.00"),
        stop_loss=Decimal("69500.00"),
        take_profit=Decimal("71500.00"),
        sl_order_id=sl_id,
        tp_order_id=tp_id,
    )
    new_sl_id, upd_warn = await sync_stop_loss_order(
        mock_exchange,
        pos,
        Decimal("69500.00"),
        rules=rules,
        trading_mode=TradingMode.TESTNET,
    )
    assert upd_warn == []
    assert new_sl_id is not None and new_sl_id != sl_id
    assert pos.sl_order_id == new_sl_id
    assert sl_id in mock_exchange.canceled_orders

    # Simulate exchange trigger fill between polling intervals
    async def fake_fetch_order(order_id: str, symbol: str):
        status = "FILLED" if order_id == new_sl_id else "NEW"
        return SimpleNamespace(status=status, filled_quantity=Decimal("0.015"), average_price=Decimal("69495.00"))

    mock_exchange.fetch_order = fake_fetch_order  # type: ignore[method-assign]
    triggered = await detect_triggered_protective_fill(mock_exchange, pos, TradingMode.TESTNET)
    assert triggered == ("STOP_LOSS", Decimal("69495.00"))

    # Cleanup on exit
    await cancel_protective_orders(mock_exchange, pos, TradingMode.TESTNET)
    assert pos.sl_order_id is None and pos.tp_order_id is None


def test_unified_atr_exit_policy_and_backtest_alignment() -> None:
    """Findings 1, 2, 3: ATR stop clamping [0.8%..2.5%], >= 2.0R TP, 1R 50% scale-out, 1.2x ATR trail, funding & exit breakdown."""
    settings = Settings(trading_mode=TradingMode.PAPER)
    # High-ATR coin (~1.4% ATR) -> 1.5x ATR stop (~2.1%) must NOT be crushed to 0.6%!
    high_vol_sig = StrategySignal(
        strategy="trend_following",
        symbol="SOL/USDT",
        action="BUY",
        confidence=0.82,
        entry_price=Decimal("100.00"),
        stop_loss=Decimal("99.40"),  # too-tight 0.6% raw stop
        take_profit=Decimal("101.20"),
        reason="ATR expansion",
        indicators={"atr": 1.40},
    )
    norm_sig = normalize_atr_targets(high_vol_sig, settings)
    stop_dist = (norm_sig.entry_price - norm_sig.stop_loss) / norm_sig.entry_price
    tp_dist = (norm_sig.take_profit - norm_sig.entry_price) / norm_sig.entry_price
    assert Decimal("0.018") <= stop_dist <= settings.max_stop_loss_pct
    assert tp_dist >= stop_dist * settings.min_reward_risk_ratio

    # Moving >= +1R triggers SCALE_OUT_1R (50%) and ratchets stop loss above entry
    step1 = evaluate_position_exit_step(
        side="LONG",
        strategy_name="trend_following",
        entry_price=Decimal("100.00"),
        current_price=Decimal("102.20"),
        stop_loss=Decimal("98.00"),
        take_profit=Decimal("104.50"),
        initial_stop_distance_pct=Decimal("0.020"),
        atr_value=Decimal("1.20"),
        peak_price=Decimal("102.20"),
        scaled_out=False,
        held_minutes=45.0,
        settings=settings,
    )
    assert step1.exit_reason == "SCALE_OUT_1R"
    assert step1.close_fraction == pytest.approx(0.5)
    assert step1.scaled_out is True
    assert step1.new_stop_loss is not None and step1.new_stop_loss > Decimal("100.00")

    # Runner continues up then pulls back 1.2x ATR -> closes via ATR_TRAILING_STOP in profit
    step2 = evaluate_position_exit_step(
        side="LONG",
        strategy_name="trend_following",
        entry_price=Decimal("100.00"),
        current_price=Decimal("103.40"),
        stop_loss=step1.new_stop_loss,
        take_profit=Decimal("106.00"),
        initial_stop_distance_pct=Decimal("0.020"),
        atr_value=Decimal("1.20"),
        peak_price=Decimal("105.00"),
        scaled_out=True,
        held_minutes=90.0,
        settings=settings,
    )
    assert step2.exit_reason == "ATR_TRAILING_STOP"
    assert step2.close_fraction == pytest.approx(1.0)

    # Verify BacktestEngine uses unified exits + funding drag
    df = CandleManager.to_dataframe(HistoricalDataLoader.generate_synthetic_regime_series(bars=350, seed=11))
    bt_no_fund = BacktestEngine(initial_capital=500.0, funding_rate_8h=0.0, settings=settings).run(
        TrendFollowingStrategy(), "SOL/USDT", df
    )
    bt_with_fund = BacktestEngine(initial_capital=500.0, funding_rate_8h=0.0005, settings=settings).run(
        TrendFollowingStrategy(), "SOL/USDT", df
    )
    assert isinstance(bt_with_fund.exit_reason_breakdown, dict)
    assert bt_with_fund.report.total_trading_costs >= bt_no_fund.report.total_trading_costs


def test_realized_payoff_ev_override_and_auto_disable_negative_expectancy() -> None:
    """Finding 1 & Phase 1: EV honors realized win/loss payoff (not optimistic nominal TP), and auto-disables after >=50 losing live trades."""
    sig = StrategySignal(
        strategy="trend_following",
        symbol="ETH/USDT",
        action="BUY",
        confidence=0.75,
        entry_price=Decimal("3000.00"),
        stop_loss=Decimal("2982.00"),   # nominal 0.6% SL
        take_profit=Decimal("3045.00"),  # nominal 1.5% TP -> looks positive on paper!
        reason="Nominal vs realized payoff test",
    )
    prob = ConfidenceEstimator().estimate(wins=28, losses=22)
    oe = OpportunityEngine()

    ev_nominal = oe.calculate_expected_value(signal=sig, prob=prob, rules=_make_rules(), ticker=_make_ticker())
    ev_realized = oe.calculate_expected_value(
        signal=sig,
        prob=prob,
        rules=_make_rules(),
        ticker=_make_ticker(),
        avg_win_override=Decimal("0.0030"),
        avg_loss_override=Decimal("0.0075"),
    )
    assert ev_nominal.is_positive_ev is True
    assert ev_realized.is_positive_ev is False
    assert ev_realized.is_statistically_valid is False

    # Auto-disable after >= 50 live trades with negative net expectancy in a strategy-regime pair
    settings = Settings(trading_mode=TradingMode.PAPER, min_trades_for_expectancy_disable=50)
    df = CandleManager.to_dataframe(HistoricalDataLoader.generate_synthetic_regime_series(bars=260, seed=3))
    live_stats = {
        ("trend_following", "SIDEWAYS"): {
            "trade_count": 52,
            "wins": 22,
            "losses": 30,
            "avg_win_pct": 0.004,
            "avg_loss_pct": 0.011,
            "net_expectancy_pct": -0.0028,
        }
    }
    stats = evaluate_separated_oos_stats(
        strategy=TrendFollowingStrategy(),
        regime_name="SIDEWAYS",
        symbol_frames=[("ETH/USDT", df)],
        live_regime_stats=live_stats,
        settings=settings,
    )
    assert stats.auto_disabled is True
    assert stats.disable_reason is not None and "expectancy" in stats.disable_reason.lower()


def test_concentration_and_same_side_correlation_caps() -> None:
    """Finding 8: 40% single-position concentration cap & max 2 same-direction correlated crypto positions."""
    settings = Settings(
        trading_mode=TradingMode.PAPER,
        max_open_positions=3,
        max_single_position_pct=Decimal("0.40"),
        max_same_side_positions=2,
        max_same_side_exposure_pct=Decimal("0.75"),
    )
    rm = RiskManager(settings=settings)
    sig = StrategySignal(
        strategy="trend_following",
        symbol="SOL/USDT",
        action="BUY",
        confidence=0.85,
        entry_price=Decimal("100.00"),
        stop_loss=Decimal("98.50"),
        take_profit=Decimal("103.50"),
        reason="Correlation guard check",
    )
    regime = RegimeAssessment(
        primary_regime=MarketRegime.BULLISH_TRENDING,
        secondary_regimes=[],
        confidence=0.85,
        volatility_pct=0.015,
        trend_strength_adx=30.0,
        trend_slope=0.003,
        reason="Uptrend",
    )
    prob = ConfidenceEstimator().estimate(wins=45, losses=15)
    ev = OpportunityEngine().calculate_expected_value(signal=sig, prob=prob, rules=_make_rules("SOL/USDT"), ticker=_make_ticker("SOL/USDT", Decimal("100")))
    opp = EvaluatedOpportunity(signal=sig, regime=regime, probability=prob, ev=ev, regime_compatible=True, score=1.0)

    # First check: single position notional must be capped <= 40% of $500 = $200
    res_single = rm.evaluate_trade(
        opportunity=opp,
        account_equity=Decimal("500.00"),
        peak_equity=Decimal("500.00"),
        day_start_equity=Decimal("500.00"),
        available_balance=Decimal("500.00"),
        open_positions_count=0,
        current_open_notional=Decimal("0"),
        rules=_make_rules("SOL/USDT"),
        db_healthy=True,
        exchange_healthy=True,
        market_data_valid=True,
        balance_verified=True,
        market_type="FUTURES",
        leverage=1,
        open_positions=[],
    )
    assert res_single.approved is True
    assert res_single.position_size.capped_notional_usd <= Decimal("200.00")

    # Second check: opening a 3rd LONG while already holding 2 LONGs must be REJECTED
    existing_longs = [
        SimpleNamespace(symbol="BTC/USDT", side="LONG", quantity=Decimal("0.002"), current_price=Decimal("70000"), entry_price=Decimal("70000")),
        SimpleNamespace(symbol="ETH/USDT", side="LONG", quantity=Decimal("0.05"), current_price=Decimal("3000"), entry_price=Decimal("3000")),
    ]
    res_corr = rm.evaluate_trade(
        opportunity=opp,
        account_equity=Decimal("500.00"),
        peak_equity=Decimal("500.00"),
        day_start_equity=Decimal("500.00"),
        available_balance=Decimal("210.00"),
        open_positions_count=2,
        current_open_notional=Decimal("290.00"),
        rules=_make_rules("SOL/USDT"),
        db_healthy=True,
        exchange_healthy=True,
        market_data_valid=True,
        balance_verified=True,
        market_type="FUTURES",
        leverage=1,
        open_positions=existing_longs,
    )
    assert res_corr.approved is False
    assert any("correlation" in r.lower() or "same-side" in r.lower() for r in res_corr.rejection_reasons)


def test_phase2_entry_filters_sideways_htf_btc_and_derivatives() -> None:
    """Finding 6 & Phase 2: Trend blocked in SIDEWAYS, HTF 1h alignment, BTC bearish alt-long filter, and crowded derivatives block."""
    neutral_deriv = DerivativesSentimentReport(symbol="SOL/USDT", crowding_state="BALANCED", allow_long=True, allow_short=True)
    crowded_long = DerivativesSentimentReport(
        symbol="SOL/USDT",
        funding_rate_pct=0.06,
        long_short_ratio=2.6,
        crowding_state="CROWDED_LONG",
        allow_long=False,
        allow_short=True,
        rationale="Crowded long positioning with elevated funding.",
    )
    bull_regime = RegimeAssessment(MarketRegime.BULLISH_TRENDING, [], 0.8, 0.015, 28.0, 0.002, "Bullish")
    sideways_regime = RegimeAssessment(MarketRegime.SIDEWAYS, [], 0.8, 0.010, 14.0, 0.0, "Sideways")

    # Construct a bearish 1h HTF series (EMA20 < EMA50) and a bullish 1h HTF series
    bear_htf = pd.DataFrame({"close": [100.0 - i * 0.4 for i in range(60)]})
    bull_htf = pd.DataFrame({"close": [100.0 + i * 0.4 for i in range(60)]})

    # 1. Trend strategy blocked in SIDEWAYS
    ok_sw, r_sw = check_phase2_entry_filters(
        symbol="SOL/USDT",
        action="BUY",
        strategy_name="trend_following",
        regime=MarketRegime.SIDEWAYS,
        htf_df=bull_htf,
        btc_regime=MarketRegime.BULLISH_TRENDING,
        btc_bearish_slope=False,
        btc_bullish_slope=True,
        deriv_report=neutral_deriv,
    )
    assert ok_sw is False and "sideways" in r_sw.lower()

    # 2. Long blocked when 1h EMA20 < EMA50
    ok_htf, r_htf = check_phase2_entry_filters(
        symbol="SOL/USDT",
        action="BUY",
        strategy_name="trend_following",
        regime=MarketRegime.BULLISH_TRENDING,
        htf_df=bear_htf,
        btc_regime=MarketRegime.BULLISH_TRENDING,
        btc_bearish_slope=False,
        btc_bullish_slope=True,
        deriv_report=neutral_deriv,
    )
    assert ok_htf is False and "1h" in r_htf.lower()

    # 3. Altcoin BUY blocked when BTC 1h slope is bearish
    ok_btc, r_btc = check_phase2_entry_filters(
        symbol="SOL/USDT",
        action="BUY",
        strategy_name="trend_following",
        regime=MarketRegime.BULLISH_TRENDING,
        htf_df=bull_htf,
        btc_regime=MarketRegime.BEARISH_TRENDING,
        btc_bearish_slope=True,
        btc_bullish_slope=False,
        deriv_report=neutral_deriv,
    )
    assert ok_btc is False and "btc" in r_btc.lower()

    # 4. Crowded long derivatives sentiment blocks fresh long entry
    ok_drv, r_drv = check_phase2_entry_filters(
        symbol="SOL/USDT",
        action="BUY",
        strategy_name="trend_following",
        regime=MarketRegime.BULLISH_TRENDING,
        htf_df=bull_htf,
        btc_regime=MarketRegime.BULLISH_TRENDING,
        btc_bearish_slope=False,
        btc_bullish_slope=True,
        deriv_report=crowded_long,
    )
    assert ok_drv is False and "crowded" in r_drv.lower()
