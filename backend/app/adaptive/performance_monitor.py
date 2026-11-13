"""Rolling multi-window performance monitoring per strategy and market regime."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from app.backtesting.performance_metrics import (
    PerformanceMetricsCalculator,
    PerformanceReport,
    TradeRecord,
)


@dataclass(frozen=True)
class StrategyWindowStats:
    strategy_name: str
    window_name: str
    regime: str
    report: PerformanceReport


class PerformanceMonitor:
    """Computes rolling window metrics (10, 30, 50 trades) and regime breakdowns."""

    WINDOWS = (10, 30, 50)

    def evaluate_strategy(
        self,
        *,
        strategy_name: str,
        trades: Sequence[TradeRecord],
        initial_capital: float = 100.0,
        result_type: str = "PAPER_TRADING_RESULTS",
    ) -> list[StrategyWindowStats]:
        stats: list[StrategyWindowStats] = []
        for w in self.WINDOWS:
            subset = list(trades[-w:])
            eq = [initial_capital]
            running = initial_capital
            for t in subset:
                running += t.pnl
                eq.append(running)
            report = PerformanceMetricsCalculator.compute(
                trades=subset,
                equity_curve=eq,
                initial_capital=initial_capital,
                result_type=result_type,
            )
            stats.append(
                StrategyWindowStats(
                    strategy_name=strategy_name,
                    window_name=f"rolling_{w}",
                    regime="ALL",
                    report=report,
                )
            )
        return stats
