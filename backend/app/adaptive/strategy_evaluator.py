"""Evaluates candidate alternative strategies using historical backtesting and walk-forward OOS validation."""

from __future__ import annotations

from dataclasses import dataclass
import pandas as pd

from app.backtesting.backtest_engine import BacktestEngine
from app.backtesting.walk_forward import WalkForwardSummary, WalkForwardValidator
from app.strategies.base_strategy import BaseStrategy


@dataclass(frozen=True)
class CandidateEvaluation:
    strategy_name: str
    walk_forward: WalkForwardSummary
    score: float
    passed: bool


class StrategyEvaluator:
    """Compares candidate strategies on identical out-of-sample walk-forward splits."""

    def __init__(self, engine: BacktestEngine | None = None, num_folds: int = 3) -> None:
        self.engine = engine or BacktestEngine()
        self.validator = WalkForwardValidator(engine=self.engine, num_folds=num_folds)

    def evaluate_candidates(
        self,
        *,
        candidates: list[BaseStrategy],
        symbol: str,
        df: pd.DataFrame,
    ) -> list[CandidateEvaluation]:
        results: list[CandidateEvaluation] = []
        for strat in candidates:
            wf = self.validator.validate(strat, symbol, df)
            # Survival-weighted composite score: OOS EV * profit_factor penalized by drawdown
            score = (
                wf.avg_oos_expected_value * 100.0
                + min(wf.avg_oos_profit_factor, 3.0) * 0.5
                - wf.oos_max_drawdown * 20.0
            )
            results.append(
                CandidateEvaluation(
                    strategy_name=strat.name,
                    walk_forward=wf,
                    score=round(score, 4),
                    passed=wf.passed_validation,
                )
            )
        results.sort(key=lambda r: r.score, reverse=True)
        return results
