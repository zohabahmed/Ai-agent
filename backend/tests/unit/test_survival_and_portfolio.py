"""Unit tests for SurvivalController, DrawdownMonitor, ExposureManager, and ProfitManager."""

from __future__ import annotations

from decimal import Decimal

from app.intelligence.regime_detector import MarketRegime
from app.portfolio.equity_calculator import EquityCalculator
from app.portfolio.profit_manager import ProfitManager
from app.risk.drawdown_monitor import DrawdownMonitor
from app.risk.exposure_manager import ExposureManager
from app.risk.survival_controller import SurvivalController, SurvivalState


def test_survival_controller_state_transitions_and_emergency_stop():
    sc = SurvivalController()
    dd_ok = DrawdownMonitor(Decimal("0.05"), Decimal("0.01")).evaluate(
        peak_equity=Decimal("100"), current_equity=Decimal("100"), day_start_equity=Decimal("100")
    )
    v_norm = sc.evaluate(
        drawdown=dd_ok,
        db_healthy=True,
        exchange_healthy=True,
        market_data_valid=True,
        balance_verified=True,
        market_regime=MarketRegime.BULLISH_TRENDING,
    )
    assert v_norm.state == SurvivalState.NORMAL
    assert v_norm.new_trades_allowed is True

    # High volatility triggers CAUTION
    v_caution = sc.evaluate(
        drawdown=dd_ok,
        db_healthy=True,
        exchange_healthy=True,
        market_data_valid=True,
        balance_verified=True,
        market_regime=MarketRegime.HIGH_VOLATILITY,
    )
    assert v_caution.state == SurvivalState.CAUTION
    assert v_caution.exposure_scale < Decimal("1.0")

    # Deterioration triggers DEFENSIVE
    v_def = sc.evaluate(
        drawdown=dd_ok,
        db_healthy=True,
        exchange_healthy=True,
        market_data_valid=True,
        balance_verified=True,
        market_regime=MarketRegime.SIDEWAYS,
        strategy_deteriorated=True,
    )
    assert v_def.state == SurvivalState.DEFENSIVE

    # Drawdown breach triggers HALTED
    dd_bad = DrawdownMonitor(Decimal("0.05"), Decimal("0.01")).evaluate(
        peak_equity=Decimal("100"), current_equity=Decimal("94"), day_start_equity=Decimal("100")
    )
    v_halt = sc.evaluate(
        drawdown=dd_bad,
        db_healthy=True,
        exchange_healthy=True,
        market_data_valid=True,
        balance_verified=True,
        market_regime=MarketRegime.BULLISH_TRENDING,
    )
    assert v_halt.state == SurvivalState.HALTED
    assert v_halt.new_trades_allowed is False

    # Emergency stop cannot be exited without operator verification
    sc.trigger_emergency_stop("Unit test halt")
    assert sc.enter_recovery(operator_verified=False) is False
    assert sc.enter_recovery(operator_verified=True) is True
    assert sc.state == SurvivalState.RECOVERY


def test_exposure_manager_blocks_leverage_and_excess_positions():
    em = ExposureManager(max_open_positions=1, max_leverage=1)
    chk_lev = em.check(
        account_equity=Decimal("100"),
        open_positions_count=0,
        current_open_notional=Decimal("0"),
        proposed_notional=Decimal("10"),
        requested_leverage=2,
    )
    assert chk_lev.allowed is False

    chk_pos = em.check(
        account_equity=Decimal("100"),
        open_positions_count=1,
        current_open_notional=Decimal("20"),
        proposed_notional=Decimal("10"),
        requested_leverage=1,
    )
    assert chk_pos.allowed is False


def test_profit_manager_never_counts_unrealized_gains_as_withdrawable():
    # Even with $50 unrealized profit, realized profit is $0 => withdrawable MUST be $0
    summary = ProfitManager.calculate_withdrawable(
        realized_net_profit=Decimal("0"),
        unrealized_pnl=Decimal("50.00"),
        available_cash=Decimal("100.00"),
        min_reserve_capital=Decimal("1.00"),
        profit_allocation_pct=Decimal("0.50"),
    )
    assert summary.withdrawable_profit == Decimal("0")

    # With $30 realized profit and $40 available cash, reserve=$1 => 50% of $30 = $15 <= $39 excess cash
    summary2 = ProfitManager.calculate_withdrawable(
        realized_net_profit=Decimal("30.00"),
        unrealized_pnl=Decimal("5.00"),
        available_cash=Decimal("40.00"),
        min_reserve_capital=Decimal("1.00"),
        profit_allocation_pct=Decimal("0.50"),
    )
    assert summary2.withdrawable_profit == Decimal("15.00000000")


def test_equity_calculator_exact_accounting():
    eq = EquityCalculator.calculate(
        initial_capital=Decimal("100"),
        available_quote=Decimal("80"),
        locked_quote=Decimal("0"),
        open_positions=[(Decimal("0.001"), Decimal("20000"), Decimal("22000"), Decimal("0.02"))],
        realized_pnl=Decimal("0"),
        total_fees=Decimal("0.02"),
    )
    assert eq.open_position_value == Decimal("22.00000000")
    assert eq.total_equity == Decimal("102.00000000")
    assert eq.unrealized_pnl == Decimal("1.98000000")
