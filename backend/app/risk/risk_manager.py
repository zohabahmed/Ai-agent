"""Risk Manager coordinating EV, Kelly, Position Sizing, Exposure, Drawdown, and Survival Controller."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from app.config import Settings, get_settings
from app.exchanges.base_exchange import MarketRules
from app.intelligence.opportunity_engine import EvaluatedOpportunity
from app.risk.drawdown_monitor import DrawdownMonitor, DrawdownStatus
from app.risk.exposure_manager import ExposureManager
from app.risk.kelly_calculator import KellyCalculator, KellyResult
from app.risk.position_sizer import PositionSizeResult, PositionSizer
from app.risk.survival_controller import SurvivalController, SurvivalState, SurvivalVerdict


@dataclass
class RiskEvaluationResult:
    approved: bool
    decision: str  # APPROVED, REJECTED
    survival: SurvivalVerdict
    kelly: KellyResult
    position_size: PositionSizeResult
    drawdown: DrawdownStatus
    rejection_reasons: list[str] = field(default_factory=list)


class RiskManager:
    """Central risk orchestrator subordinate to SurvivalController."""

    def __init__(
        self,
        settings: Settings | None = None,
        survival_controller: SurvivalController | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.survival = survival_controller or SurvivalController()
        self.kelly_calc = KellyCalculator(
            kelly_mode=self.settings.kelly_mode,
            max_risk_per_trade=self.settings.max_risk_per_trade,
        )
        self.position_sizer = PositionSizer()
        self.drawdown_monitor = DrawdownMonitor(
            max_drawdown=self.settings.max_drawdown,
            max_daily_loss=self.settings.max_daily_loss,
        )
        self.exposure_manager = ExposureManager(
            max_open_positions=self.settings.max_open_positions,
            max_leverage=self.settings.max_leverage,
        )

    def evaluate_trade(
        self,
        *,
        opportunity: EvaluatedOpportunity,
        account_equity: Decimal,
        peak_equity: Decimal,
        day_start_equity: Decimal,
        available_balance: Decimal,
        open_positions_count: int,
        current_open_notional: Decimal,
        rules: MarketRules,
        db_healthy: bool,
        exchange_healthy: bool,
        market_data_valid: bool,
        balance_verified: bool,
        strategy_deteriorated: bool = False,
        execution_error_pending: bool = False,
        market_type: str = "SPOT",
        leverage: int = 1,
    ) -> RiskEvaluationResult:
        reasons: list[str] = []
        dd = self.drawdown_monitor.evaluate(
            peak_equity=peak_equity,
            current_equity=account_equity,
            day_start_equity=day_start_equity,
        )
        verdict = self.survival.evaluate(
            drawdown=dd,
            db_healthy=db_healthy,
            exchange_healthy=exchange_healthy,
            market_data_valid=market_data_valid,
            balance_verified=balance_verified,
            market_regime=opportunity.regime.primary_regime,
            strategy_deteriorated=strategy_deteriorated,
            execution_error_pending=execution_error_pending,
        )

        if not verdict.new_trades_allowed:
            reasons.extend(verdict.halt_reasons)

        sig = opportunity.signal
        ev = opportunity.ev
        prob = opportunity.probability

        if sig.action == "HOLD":
            reasons.append("Strategy signal is HOLD.")
        if not opportunity.regime_compatible:
            reasons.append(
                f"Strategy '{sig.strategy}' is incompatible with regime {opportunity.regime.primary_regime.value}."
            )
        if not ev.is_positive_ev:
            reasons.append(f"Expected value is non-positive ({ev.net_ev_pct:.6f}).")
        if not ev.is_statistically_valid:
            reasons.append(f"Statistical validity check failed (n={prob.sample_size}, width={prob.uncertainty_width:.3f}).")

        if verdict.require_higher_confidence and prob.conservative_win_probability < 0.50:
            reasons.append("Defensive/Caution state requires conservative win probability >= 0.50.")

        stop_dist_pct = (
            float(abs(sig.entry_price - sig.stop_loss) / sig.entry_price)
            if sig.entry_price > 0
            else 0.0
        )
        kelly = self.kelly_calc.calculate_binary(
            win_probability=float(ev.win_probability),
            reward_risk_ratio=float(ev.reward_risk_ratio),
            uncertainty_width=prob.uncertainty_width,
            expected_cost_pct=float(ev.total_cost_pct),
            stop_distance_pct=stop_dist_pct,
        )
        if not kelly.is_valid:
            reasons.append(kelly.reason)

        norm_mkt = market_type.upper()
        effective_leverage = max(1, min(int(leverage), 5)) if norm_mkt == "FUTURES" else 1

        pos_size = self.position_sizer.calculate(
            account_equity=account_equity,
            available_balance=available_balance,
            kelly_risk_fraction=kelly.fractional_kelly,
            entry_price=sig.entry_price,
            stop_loss_price=sig.stop_loss,
            rules=rules,
            regime_scale=verdict.exposure_scale,
            leverage=effective_leverage,
        )
        if not pos_size.is_viable:
            reasons.append(pos_size.reason)

        exp_mgr = (
            ExposureManager(
                max_open_positions=self.settings.max_open_positions,
                max_leverage=effective_leverage,
            )
            if norm_mkt == "FUTURES"
            else self.exposure_manager
        )
        exp = exp_mgr.check(
            account_equity=account_equity,
            open_positions_count=open_positions_count,
            current_open_notional=current_open_notional,
            proposed_notional=pos_size.capped_notional_usd,
            requested_leverage=effective_leverage,
            market_type=norm_mkt,
        )
        if not exp.allowed:
            reasons.extend(exp.reasons)

        approved = len(reasons) == 0 and verdict.state != SurvivalState.HALTED
        return RiskEvaluationResult(
            approved=approved,
            decision="APPROVED" if approved else "REJECTED",
            survival=verdict,
            kelly=kelly,
            position_size=pos_size,
            drawdown=dd,
            rejection_reasons=reasons,
        )
