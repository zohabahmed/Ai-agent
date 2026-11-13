"""Unit tests for Kelly Criterion math, fractional Kelly caps, and $1 survival position sizing."""

from __future__ import annotations

from decimal import Decimal

from app.config import KellyMode
from app.exchanges.base_exchange import MarketRules
from app.risk.kelly_calculator import KellyCalculator
from app.risk.position_sizer import PositionSizer


def _binance_btc_rules() -> MarketRules:
    return MarketRules(
        symbol="BTC/USDT",
        base_asset="BTC",
        quote_asset="USDT",
        min_quantity=Decimal("0.00001"),
        min_notional=Decimal("5.00"),
        quantity_precision=5,
        price_precision=2,
        maker_fee=Decimal("0.001"),
        taker_fee=Decimal("0.001"),
        is_active=True,
    )


def test_binary_kelly_exact_math():
    # p = 0.60, b = 2.0 => f* = (2*0.6 - 0.4)/2 = 0.40
    calc = KellyCalculator(kelly_mode=KellyMode.CONSERVATIVE, max_risk_per_trade=Decimal("0.50"))
    res = calc.calculate_binary(
        win_probability=0.60,
        reward_risk_ratio=2.0,
        uncertainty_width=0.0,
        expected_cost_pct=0.0,
        stop_distance_pct=0.02,
    )
    assert res.is_valid is True
    assert abs(float(res.full_kelly) - 0.40) < 1e-5
    # Conservative multiplier 0.10 => 0.04
    assert abs(float(res.fractional_kelly) - 0.04) < 1e-5


def test_kelly_rejects_negative_and_zero_edge():
    calc = KellyCalculator(kelly_mode=KellyMode.CONSERVATIVE)
    # p = 0.30, b = 1.5 => f* = (0.45 - 0.70)/1.5 < 0
    neg = calc.calculate_binary(win_probability=0.30, reward_risk_ratio=1.5, uncertainty_width=0.05)
    assert neg.is_valid is False
    assert neg.fractional_kelly == Decimal("0")

    # Breakeven p = 0.50, b = 1.0 => f* = 0
    zero = calc.calculate_binary(win_probability=0.50, reward_risk_ratio=1.0, uncertainty_width=0.05)
    assert zero.is_valid is False
    assert zero.fractional_kelly == Decimal("0")


def test_kelly_never_auto_enables_aggressive_mode():
    calc = KellyCalculator(kelly_mode=KellyMode.AGGRESSIVE)
    assert calc.kelly_mode != KellyMode.AGGRESSIVE
    assert calc.multiplier <= Decimal("0.20")


def test_kelly_rejects_excessive_probability_uncertainty():
    calc = KellyCalculator(kelly_mode=KellyMode.CONSERVATIVE, max_uncertainty_width=0.30)
    res = calc.calculate_binary(win_probability=0.65, reward_risk_ratio=2.0, uncertainty_width=0.45)
    assert res.is_valid is False
    assert res.fractional_kelly == Decimal("0")


def test_empirical_expected_log_growth_optimizer():
    calc = KellyCalculator(kelly_mode=KellyMode.CONSERVATIVE, max_risk_per_trade=Decimal("0.05"))
    # Positive drift return series
    returns = [0.03, -0.01, 0.025, -0.01, 0.02, 0.015, -0.008, 0.03, -0.012, 0.022] * 3
    f_frac, g_val = calc.optimize_empirical_log_growth(returns)
    assert f_frac > Decimal("0")
    assert g_val > 0.0

    # Negative drift returns -> 0 allocation
    bad_returns = [-0.02, 0.01, -0.03, -0.01, 0.005] * 4
    f_bad, g_bad = calc.optimize_empirical_log_growth(bad_returns)
    assert f_bad == Decimal("0")
    assert g_bad == 0.0


def test_one_dollar_account_preserves_capital_instead_of_forcing_min_notional():
    sizer = PositionSizer()
    rules = _binance_btc_rules()  # min_notional = $5.00
    res = sizer.calculate(
        account_equity=Decimal("1.00"),
        available_balance=Decimal("1.00"),
        kelly_risk_fraction=Decimal("0.0025"),
        entry_price=Decimal("65000.00"),
        stop_loss_price=Decimal("63700.00"),  # 2% stop => target notional = $0.125
        rules=rules,
    )
    assert res.is_viable is False
    assert "Economically non-viable" in res.reason
    assert res.risk_budget_usd <= Decimal("0.0025")


def test_position_sizer_respects_stop_distance_and_caps():
    sizer = PositionSizer()
    rules = _binance_btc_rules()
    # Equity $5000, risk fraction 0.0025 => risk budget $12.50, 2% stop => $625 notional
    res = sizer.calculate(
        account_equity=Decimal("5000.00"),
        available_balance=Decimal("5000.00"),
        kelly_risk_fraction=Decimal("0.0025"),
        entry_price=Decimal("50000.00"),
        stop_loss_price=Decimal("49000.00"),
        rules=rules,
    )
    assert res.is_viable is True
    assert res.risk_budget_usd == Decimal("12.500000")
    assert res.capped_notional_usd <= Decimal("625.00")
    assert res.quantity == Decimal("0.01250")
