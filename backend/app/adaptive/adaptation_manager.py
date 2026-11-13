"""Autonomous Adaptation Manager orchestrating drift detection, validation, switching, and rollback."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adaptive.drift_detector import DriftDetector, DriftReport
from app.adaptive.performance_monitor import PerformanceMonitor
from app.adaptive.strategy_evaluator import CandidateEvaluation, StrategyEvaluator
from app.adaptive.strategy_versioning import StrategyVersioningService
from app.backtesting.performance_metrics import TradeRecord
from app.database.models import Strategy
from app.database.repositories.trading_repo import TradingRepository
from app.intelligence.ai_advisor import AIAdvisor
from app.strategies.strategy_registry import StrategyRegistry


@dataclass
class AdaptationOutcome:
    deterioration_detected: bool
    drift_report: DriftReport
    diagnosis: dict[str, Any] | None = None
    switched: bool = False
    activated_strategy: str | None = None
    disabled_strategy: str | None = None
    candidate_evaluations: list[CandidateEvaluation] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


class AdaptationManager:
    """
    Executes the controlled adaptation workflow:
      Detect drift -> Investigate -> Evaluate alternatives via Walk-Forward OOS ->
      Activate validated replacement (never unvalidated) -> Record version & audit.
    """

    def __init__(
        self,
        *,
        registry: StrategyRegistry,
        drift_detector: DriftDetector | None = None,
        evaluator: StrategyEvaluator | None = None,
        perf_monitor: PerformanceMonitor | None = None,
    ) -> None:
        self.registry = registry
        self.drift_detector = drift_detector or DriftDetector()
        self.evaluator = evaluator or StrategyEvaluator()
        self.perf_monitor = perf_monitor or PerformanceMonitor()

    async def evaluate_and_adapt(
        self,
        *,
        session: AsyncSession,
        active_strategy_name: str,
        recent_trades: list[TradeRecord],
        historical_df: pd.DataFrame,
        symbol: str,
        current_regime: str,
    ) -> AdaptationOutcome:
        repo = TradingRepository(session)
        returns = [t.return_pct for t in recent_trades]
        drift = self.drift_detector.detect(returns)

        if not drift.deteriorated:
            return AdaptationOutcome(
                deterioration_detected=False,
                drift_report=drift,
                notes=["Strategy performance within statistical tolerance; no adaptation needed."],
            )

        stats = self.perf_monitor.evaluate_strategy(
            strategy_name=active_strategy_name,
            trades=recent_trades,
        )
        latest_rep = stats[0].report if stats else None
        metrics_dict = {
            "win_rate": latest_rep.win_rate if latest_rep else 0.0,
            "expected_value": latest_rep.expected_value if latest_rep else 0.0,
            "profit_factor": latest_rep.profit_factor if latest_rep else 0.0,
            "max_drawdown": latest_rep.max_drawdown_pct if latest_rep else 0.0,
        }
        diagnosis = AIAdvisor.diagnose_deterioration(
            strategy_name=active_strategy_name,
            metrics=metrics_dict,
            regime=current_regime,
            drift_reasons=drift.reasons,
        )

        # Disable deteriorating strategy immediately in registry & DB
        self.registry.disable(active_strategy_name)
        strat_row_res = await session.execute(select(Strategy).where(Strategy.name == active_strategy_name))
        strat_row = strat_row_res.scalar_one_or_none()
        if strat_row:
            strat_row.status = "DISABLED"

        # Evaluate alternative strategies via walk-forward OOS validation
        candidates = [s for s in self.registry.list_all() if s.name != active_strategy_name]
        evals = self.evaluator.evaluate_candidates(candidates=candidates, symbol=symbol, df=historical_df)
        passing = [e for e in evals if e.passed]

        switched = False
        activated_name: str | None = None
        notes: list[str] = [f"Disabled deteriorating strategy '{active_strategy_name}'."]

        if passing:
            best = passing[0]
            activated_name = best.strategy_name
            self.registry.enable(activated_name)
            switched = True
            notes.append(
                f"Activated validated replacement '{activated_name}' (score={best.score}, {best.walk_forward.reason})."
            )

            cand_res = await session.execute(select(Strategy).where(Strategy.name == activated_name))
            cand_row = cand_res.scalar_one_or_none()
            if cand_row:
                cand_row.status = "ACTIVE"
                strat_obj = self.registry.get(activated_name)
                cand_params = strat_obj.params if strat_obj else {}
                if cand_row.parameters != cand_params:
                    await StrategyVersioningService.record_version(
                        session,
                        strategy=cand_row,
                        parameters=cand_params,
                        change_reason=f"Adaptive switch replacing deteriorated '{active_strategy_name}'",
                        validation_summary={
                            "avg_oos_ev": best.walk_forward.avg_oos_expected_value,
                            "avg_oos_pf": best.walk_forward.avg_oos_profit_factor,
                            "oos_max_dd": best.walk_forward.oos_max_drawdown,
                        },
                        status="ACTIVE",
                        created_by="adaptive_engine",
                    )
        else:
            notes.append("No alternative strategy passed walk-forward validation. Entering DEFENSIVE/cash preservation.")

        await repo.log_agent_event(
            event_type="STRATEGY_ADAPTATION",
            component="adaptive_engine",
            severity="WARNING",
            message="; ".join(notes),
            payload={
                "disabled_strategy": active_strategy_name,
                "activated_strategy": activated_name,
                "drift_score": drift.drift_score,
                "diagnosis": diagnosis,
            },
        )
        return AdaptationOutcome(
            deterioration_detected=True,
            drift_report=drift,
            diagnosis=diagnosis,
            switched=switched,
            activated_strategy=activated_name,
            disabled_strategy=active_strategy_name,
            candidate_evaluations=evals,
            notes=notes,
        )
