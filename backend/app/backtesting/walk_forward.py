"""Walk-Forward Validation Engine (chronological train/validation/test splits)."""

from __future__ import annotations

from dataclasses import dataclass
import pandas as pd

from app.backtesting.backtest_engine import BacktestEngine
from app.backtesting.performance_metrics import PerformanceReport
from app.strategies.base_strategy import BaseStrategy


@dataclass(frozen=True)
class WalkForwardFold:
    fold_index: int
    in_sample_bars: int
    out_of_sample_bars: int
    in_sample_report: PerformanceReport
    out_of_sample_report: PerformanceReport


@dataclass(frozen=True)
class WalkForwardSummary:
    strategy_name: str
    symbol: str
    folds: list[WalkForwardFold]
    avg_oos_expected_value: float
    avg_oos_win_rate: float
    avg_oos_profit_factor: float
    oos_max_drawdown: float
    passed_validation: bool
    reason: str


class WalkForwardValidator:
    """Performs chronological anchored/rolling walk-forward out-of-sample testing."""

    def __init__(self, engine: BacktestEngine | None = None, num_folds: int = 3) -> None:
        self.engine = engine or BacktestEngine()
        self.num_folds = max(2, num_folds)

    def validate(self, strategy: BaseStrategy, symbol: str, df: pd.DataFrame) -> WalkForwardSummary:
        n = len(df)
        min_req = max(strategy.min_candles + 20, 100)
        if n < min_req * 2:
            return WalkForwardSummary(
                strategy_name=strategy.name,
                symbol=symbol,
                folds=[],
                avg_oos_expected_value=0.0,
                avg_oos_win_rate=0.0,
                avg_oos_profit_factor=0.0,
                oos_max_drawdown=0.0,
                passed_validation=False,
                reason=f"Insufficient historical bars ({n}) for walk-forward validation.",
            )

        # Split chronologically: first 40% initial train, remaining 60% divided into OOS folds
        train_end = int(n * 0.40)
        oos_span = max(min_req, (n - train_end) // self.num_folds)
        folds: list[WalkForwardFold] = []

        cursor = train_end
        for idx in range(self.num_folds):
            if cursor + strategy.min_candles + 5 >= n:
                break
            end_oos = min(n, cursor + oos_span)
            is_df = df.iloc[:cursor].reset_index(drop=True)
            # Include warm-up lookback for indicators without leaking future bars
            warmup_start = max(0, cursor - strategy.min_candles)
            oos_df = df.iloc[warmup_start:end_oos].reset_index(drop=True)

            is_res = self.engine.run(strategy, symbol, is_df)
            oos_res = self.engine.run(strategy, symbol, oos_df)
            folds.append(
                WalkForwardFold(
                    fold_index=idx + 1,
                    in_sample_bars=len(is_df),
                    out_of_sample_bars=end_oos - cursor,
                    in_sample_report=is_res.report,
                    out_of_sample_report=oos_res.report,
                )
            )
            cursor = end_oos

        if not folds:
            return WalkForwardSummary(
                strategy_name=strategy.name,
                symbol=symbol,
                folds=[],
                avg_oos_expected_value=0.0,
                avg_oos_win_rate=0.0,
                avg_oos_profit_factor=0.0,
                oos_max_drawdown=0.0,
                passed_validation=False,
                reason="Could not construct valid out-of-sample folds.",
            )

        avg_ev = sum(f.out_of_sample_report.expected_value for f in folds) / len(folds)
        avg_wr = sum(f.out_of_sample_report.win_rate for f in folds) / len(folds)
        avg_pf = sum(f.out_of_sample_report.profit_factor for f in folds) / len(folds)
        max_dd = max(f.out_of_sample_report.max_drawdown_pct for f in folds)
        total_oos_trades = sum(f.out_of_sample_report.trade_count for f in folds)

        passed = avg_ev > 0 and max_dd < self.engine.max_drawdown_limit and total_oos_trades >= 1
        reason = (
            f"OOS Folds={len(folds)}, trades={total_oos_trades}, avg_EV={avg_ev:.5f}, "
            f"avg_PF={avg_pf:.2f}, max_DD={max_dd:.2%}"
        )
        return WalkForwardSummary(
            strategy_name=strategy.name,
            symbol=symbol,
            folds=folds,
            avg_oos_expected_value=round(avg_ev, 6),
            avg_oos_win_rate=round(avg_wr, 4),
            avg_oos_profit_factor=round(avg_pf, 4),
            oos_max_drawdown=round(max_dd, 6),
            passed_validation=passed,
            reason=reason,
        )
