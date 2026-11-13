"""Expected Value Engine & Opportunity Scoring."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.exchanges.base_exchange import MarketRules, Ticker
from app.intelligence.confidence_estimator import ConfidenceEstimator, ProbabilityEstimate
from app.intelligence.regime_detector import MarketRegime, RegimeAssessment
from app.strategies.base_strategy import StrategySignal


@dataclass(frozen=True)
class ExpectedValueAnalysis:
    win_probability: Decimal
    loss_probability: Decimal
    avg_win_pct: Decimal
    avg_loss_pct: Decimal
    reward_risk_ratio: Decimal
    entry_fee_pct: Decimal
    exit_fee_pct: Decimal
    spread_pct: Decimal
    slippage_pct: Decimal
    total_cost_pct: Decimal
    gross_ev_pct: Decimal
    net_ev_pct: Decimal
    is_positive_ev: bool
    is_statistically_valid: bool
    reason: str


@dataclass(frozen=True)
class EvaluatedOpportunity:
    signal: StrategySignal
    regime: RegimeAssessment
    probability: ProbabilityEstimate
    ev: ExpectedValueAnalysis
    regime_compatible: bool
    score: float


class OpportunityEngine:
    """Calculates fee/spread/slippage-adjusted Expected Value and filters statistically invalid setups."""

    def __init__(self, estimator: ConfidenceEstimator | None = None) -> None:
        self.estimator = estimator or ConfidenceEstimator()

    def calculate_expected_value(
        self,
        *,
        signal: StrategySignal,
        prob: ProbabilityEstimate,
        rules: MarketRules,
        ticker: Ticker,
        estimated_slippage_bps: Decimal = Decimal("5.0"),
        avg_win_override: Decimal | None = None,
        avg_loss_override: Decimal | None = None,
    ) -> ExpectedValueAnalysis:
        entry = signal.entry_price
        if entry <= 0 or signal.stop_loss <= 0 or signal.take_profit <= 0:
            return ExpectedValueAnalysis(
                win_probability=Decimal("0"),
                loss_probability=Decimal("1"),
                avg_win_pct=Decimal("0"),
                avg_loss_pct=Decimal("0"),
                reward_risk_ratio=Decimal("0"),
                entry_fee_pct=rules.taker_fee,
                exit_fee_pct=rules.taker_fee,
                spread_pct=Decimal("0"),
                slippage_pct=Decimal("0"),
                total_cost_pct=Decimal("0"),
                gross_ev_pct=Decimal("-1"),
                net_ev_pct=Decimal("-1"),
                is_positive_ev=False,
                is_statistically_valid=False,
                reason="Invalid signal price levels.",
            )

        raw_win_pct = abs(signal.take_profit - entry) / entry
        raw_loss_pct = abs(entry - signal.stop_loss) / entry
        avg_win_pct = avg_win_override if avg_win_override and avg_win_override > 0 else raw_win_pct
        avg_loss_pct = avg_loss_override if avg_loss_override and avg_loss_override > 0 else raw_loss_pct

        if avg_loss_pct <= 0:
            avg_loss_pct = Decimal("0.01")

        # Use conservative lower-bound win probability for survival-first EV
        p = Decimal(str(prob.conservative_win_probability))
        q = Decimal("1") - p

        entry_fee = rules.taker_fee
        exit_fee = rules.taker_fee
        spread_pct = ticker.spread_bps / Decimal("10000")
        slippage_pct = (estimated_slippage_bps * Decimal("2")) / Decimal("10000")  # entry + exit slippage
        total_cost = entry_fee + exit_fee + spread_pct + slippage_pct

        gross_ev = (p * avg_win_pct) - (q * avg_loss_pct)
        net_ev = gross_ev - total_cost
        rr = avg_win_pct / avg_loss_pct

        stat_valid = (
            prob.meets_min_sample
            and prob.uncertainty_width <= self.estimator.max_uncertainty_width
            and net_ev > Decimal("0")
        )
        reason = (
            f"p_cons={p:.3f}, W={avg_win_pct:.4f}, L={avg_loss_pct:.4f}, "
            f"C={total_cost:.4f} (fees+spread+slip), net_EV={net_ev:.5f}"
        )
        return ExpectedValueAnalysis(
            win_probability=p,
            loss_probability=q,
            avg_win_pct=avg_win_pct,
            avg_loss_pct=avg_loss_pct,
            reward_risk_ratio=rr,
            entry_fee_pct=entry_fee,
            exit_fee_pct=exit_fee,
            spread_pct=spread_pct,
            slippage_pct=slippage_pct,
            total_cost_pct=total_cost,
            gross_ev_pct=gross_ev,
            net_ev_pct=net_ev,
            is_positive_ev=net_ev > Decimal("0"),
            is_statistically_valid=stat_valid,
            reason=reason,
        )

    @staticmethod
    def is_regime_compatible(strategy_preferred: list[str], regime: RegimeAssessment) -> bool:
        if regime.primary_regime == MarketRegime.ABNORMAL_UNSTABLE:
            return False
        if regime.primary_regime.value in strategy_preferred:
            return True
        return any(r.value in strategy_preferred for r in regime.secondary_regimes)
