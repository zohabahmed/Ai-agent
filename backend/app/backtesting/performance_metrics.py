"""Quantitative performance metrics calculator (Sharpe, Sortino, Profit Factor, Drawdown, EV)."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import math
from typing import Sequence


@dataclass(frozen=True)
class TradeRecord:
    pnl: float
    return_pct: float
    fees: float
    duration_bars: int
    regime: str = "UNKNOWN"


@dataclass(frozen=True)
class PerformanceReport:
    result_type: str  # BACKTEST_RESULTS, PAPER_TRADING_RESULTS, TESTNET_RESULTS, LIVE_TRADING_RESULTS
    is_simulated: bool
    initial_capital: float
    final_equity: float
    total_return_pct: float
    net_profit: float
    trade_count: int
    win_rate: float
    avg_win: float
    avg_loss: float
    profit_factor: float
    expected_value: float
    sharpe_ratio: float
    sortino_ratio: float
    max_drawdown_pct: float
    total_trading_costs: float
    avg_trade_duration_bars: float
    consecutive_losses: int


class PerformanceMetricsCalculator:
    """Computes full risk-adjusted performance reports from closed trade histories."""

    @staticmethod
    def compute(
        *,
        trades: Sequence[TradeRecord],
        equity_curve: Sequence[float],
        initial_capital: float,
        result_type: str = "BACKTEST_RESULTS",
    ) -> PerformanceReport:
        is_sim = result_type != "LIVE_TRADING_RESULTS"
        final_eq = equity_curve[-1] if equity_curve else initial_capital
        net_profit = final_eq - initial_capital
        total_ret_pct = (net_profit / initial_capital * 100.0) if initial_capital > 0 else 0.0

        # Max drawdown from equity curve
        peak = initial_capital
        max_dd = 0.0
        for eq in equity_curve:
            if eq > peak:
                peak = eq
            if peak > 0:
                dd = (peak - eq) / peak
                if dd > max_dd:
                    max_dd = dd

        n = len(trades)
        if n == 0:
            return PerformanceReport(
                result_type=result_type,
                is_simulated=is_sim,
                initial_capital=round(initial_capital, 6),
                final_equity=round(final_eq, 6),
                total_return_pct=round(total_ret_pct, 4),
                net_profit=round(net_profit, 6),
                trade_count=0,
                win_rate=0.0,
                avg_win=0.0,
                avg_loss=0.0,
                profit_factor=0.0,
                expected_value=0.0,
                sharpe_ratio=0.0,
                sortino_ratio=0.0,
                max_drawdown_pct=round(max_dd, 6),
                total_trading_costs=0.0,
                avg_trade_duration_bars=0.0,
                consecutive_losses=0,
            )

        wins = [t.pnl for t in trades if t.pnl > 0]
        losses = [abs(t.pnl) for t in trades if t.pnl <= 0]
        win_rate = len(wins) / n
        avg_win = sum(wins) / len(wins) if wins else 0.0
        avg_loss = sum(losses) / len(losses) if losses else 0.0
        gross_profit = sum(wins)
        gross_loss = sum(losses)
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (10.0 if gross_profit > 0 else 0.0)
        ev = sum(t.pnl for t in trades) / n
        total_costs = sum(t.fees for t in trades)
        avg_dur = sum(t.duration_bars for t in trades) / n

        # Max trailing consecutive losses
        curr_streak = 0
        for t in reversed(trades):
            if t.pnl <= 0:
                curr_streak += 1
            else:
                break

        rets = [t.return_pct for t in trades]
        mean_r = sum(rets) / n
        if n >= 2:
            var_r = sum((r - mean_r) ** 2 for r in rets) / (n - 1)
            std_r = math.sqrt(var_r)
            downside_sq = [min(0.0, r) ** 2 for r in rets]
            downside_dev = math.sqrt(sum(downside_sq) / n)
            sharpe = (mean_r / std_r) * math.sqrt(n) if std_r > 1e-12 else 0.0
            sortino = (mean_r / downside_dev) * math.sqrt(n) if downside_dev > 1e-12 else 0.0
        else:
            sharpe = 0.0
            sortino = 0.0

        return PerformanceReport(
            result_type=result_type,
            is_simulated=is_sim,
            initial_capital=round(initial_capital, 6),
            final_equity=round(final_eq, 6),
            total_return_pct=round(total_ret_pct, 4),
            net_profit=round(net_profit, 6),
            trade_count=n,
            win_rate=round(win_rate, 4),
            avg_win=round(avg_win, 6),
            avg_loss=round(avg_loss, 6),
            profit_factor=round(profit_factor, 4),
            expected_value=round(ev, 6),
            sharpe_ratio=round(sharpe, 4),
            sortino_ratio=round(sortino, 4),
            max_drawdown_pct=round(max_dd, 6),
            total_trading_costs=round(total_costs, 6),
            avg_trade_duration_bars=round(avg_dur, 2),
            consecutive_losses=curr_streak,
        )
