"""AI-Assisted Decision & Explanation Engine (evidence-grounded, never an oracle)."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from app.intelligence.opportunity_engine import EvaluatedOpportunity


@dataclass(frozen=True)
class StructuredDecisionRecord:
    action: str
    symbol: str
    strategy: str
    market_regime: str
    win_probability: float
    expected_value: float
    kelly_fraction: float
    risk_status: str
    reason: str
    ten_point_audit: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "symbol": self.symbol,
            "strategy": self.strategy,
            "market_regime": self.market_regime,
            "win_probability": self.win_probability,
            "expected_value": self.expected_value,
            "kelly_fraction": self.kelly_fraction,
            "risk_status": self.risk_status,
            "reason": self.reason,
            "ten_point_audit": self.ten_point_audit,
        }


class AIAdvisor:
    """
    Produces auditable, numerically backed decision records and strategy deterioration diagnostics.
    Cannot override risk/survival rules or modify production trading code.
    """

    @staticmethod
    def build_decision_record(
        *,
        opportunity: EvaluatedOpportunity,
        kelly_fraction: Decimal,
        risk_status: str,
        survival_ok: bool,
        rejection_reasons: list[str],
    ) -> StructuredDecisionRecord:
        sig = opportunity.signal
        ev = opportunity.ev
        reg = opportunity.regime
        prob = opportunity.probability

        should_hold = (
            sig.action == "HOLD"
            or not ev.is_positive_ev
            or not ev.is_statistically_valid
            or kelly_fraction <= 0
            or not survival_ok
        )

        final_action = "HOLD" if should_hold else sig.action
        audit = {
            "1_market_regime": f"{reg.primary_regime.value} (conf={reg.confidence:.2f})",
            "2_selected_strategy": sig.strategy,
            "3_supporting_evidence": sig.reason,
            "4_estimated_win_probability": f"{prob.conservative_win_probability:.4f} (CI=[{prob.ci_low:.3f},{prob.ci_high:.3f}], n={prob.sample_size})",
            "5_expected_return_ev": f"{float(ev.net_ev_pct):.6f}",
            "6_fees_and_spread_cost": f"{float(ev.total_cost_pct):.6f}",
            "7_expected_downside_stop_pct": f"{float(ev.avg_loss_pct):.4f}",
            "8_fractional_kelly_allocation": f"{float(kelly_fraction):.6f}",
            "9_satisfies_survival_restrictions": survival_ok,
            "10_no_trade_better": should_hold,
            "rejection_reasons": rejection_reasons,
        }
        reason = (
            f"NO TRADE: {'; '.join(rejection_reasons)}"
            if should_hold and rejection_reasons
            else sig.reason
        )
        return StructuredDecisionRecord(
            action=final_action,
            symbol=sig.symbol,
            strategy=sig.strategy,
            market_regime=reg.primary_regime.value,
            win_probability=round(float(ev.win_probability), 6),
            expected_value=round(float(ev.net_ev_pct), 6),
            kelly_fraction=round(float(kelly_fraction), 6),
            risk_status=risk_status,
            reason=reason,
            ten_point_audit=audit,
        )

    @staticmethod
    def diagnose_deterioration(
        *,
        strategy_name: str,
        metrics: dict[str, Any],
        regime: str,
        drift_reasons: list[str],
    ) -> dict[str, Any]:
        win_rate = float(metrics.get("win_rate", 0.0))
        ev = float(metrics.get("expected_value", 0.0))
        pf = float(metrics.get("profit_factor", 0.0))
        dd = float(metrics.get("max_drawdown", 0.0))

        causes: list[str] = []
        if ev <= 0:
            causes.append(f"Negative rolling expected value ({ev:.6f}).")
        if pf < 1.0:
            causes.append(f"Profit factor below breakeven ({pf:.2f}).")
        if win_rate < 0.40:
            causes.append(f"Win rate compressed to {win_rate:.1%}.")
        if dd > 0.03:
            causes.append(f"Elevated strategy drawdown ({dd:.2%}).")
        causes.extend(drift_reasons)

        return {
            "strategy": strategy_name,
            "affected_regime": regime,
            "summary": f"Strategy '{strategy_name}' shows statistically significant deterioration in {regime}.",
            "causes": causes,
            "recommended_action": "Reduce allocation / enter DEFENSIVE evaluation and run walk-forward comparison against standby strategies.",
        }
