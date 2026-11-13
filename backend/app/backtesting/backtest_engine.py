"""Zero look-ahead bar-by-bar Backtest Engine with realistic costs, slippage, and survival limits."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import pandas as pd

from app.backtesting.performance_metrics import (
    PerformanceMetricsCalculator,
    PerformanceReport,
    TradeRecord,
)
from app.strategies.base_strategy import BaseStrategy


@dataclass
class BacktestResult:
    strategy_name: str
    symbol: str
    report: PerformanceReport
    trades: list[TradeRecord]
    equity_curve: list[float]


class BacktestEngine:
    """
    Reproducible, zero-look-ahead backtest engine.
    - Signal evaluated strictly on closed bar i -> executed on bar i+1 open + spread/slippage.
    - Enforces stop-loss, take-profit, maker/taker fees, and max drawdown halt.
    """

    def __init__(
        self,
        *,
        initial_capital: float = 100.0,
        fee_rate: float = 0.001,
        slippage_bps: float = 5.0,
        spread_bps: float = 4.0,
        risk_per_trade: float = 0.0025,
        max_drawdown_limit: float = 0.05,
    ) -> None:
        self.initial_capital = initial_capital
        self.fee_rate = fee_rate
        self.slippage_pct = slippage_bps / 10000.0
        self.half_spread_pct = (spread_bps / 2.0) / 10000.0
        self.risk_per_trade = risk_per_trade
        self.max_drawdown_limit = max_drawdown_limit

    def run(
        self,
        strategy: BaseStrategy,
        symbol: str,
        df: pd.DataFrame,
        *,
        allow_short: bool = False,
    ) -> BacktestResult:
        equity = self.initial_capital
        peak_equity = self.initial_capital
        equity_curve: list[float] = [equity]
        trades: list[TradeRecord] = []

        in_pos = False
        pos_side = "BUY"
        qty = 0.0
        entry_price = 0.0
        entry_fee = 0.0
        stop_loss = 0.0
        take_profit = 0.0
        entry_bar = 0
        halted = False

        start_idx = max(strategy.min_candles, 60)
        if len(df) <= start_idx + 1:
            rep = PerformanceMetricsCalculator.compute(
                trades=[], equity_curve=equity_curve, initial_capital=self.initial_capital
            )
            return BacktestResult(strategy.name, symbol, rep, [], equity_curve)

        for i in range(start_idx, len(df) - 1):
            bar = df.iloc[i]
            next_bar = df.iloc[i + 1]

            if in_pos:
                bar_low = float(bar["low"])
                bar_high = float(bar["high"])
                exit_price = None

                if pos_side == "BUY":
                    # Conservative assumption: check stop-loss before take-profit intra-bar
                    if bar_low <= stop_loss:
                        exit_price = stop_loss * (1.0 - self.slippage_pct - self.half_spread_pct)
                    elif bar_high >= take_profit:
                        exit_price = take_profit * (1.0 - self.slippage_pct - self.half_spread_pct)
                else:
                    if bar_high >= stop_loss:
                        exit_price = stop_loss * (1.0 + self.slippage_pct + self.half_spread_pct)
                    elif bar_low <= take_profit:
                        exit_price = take_profit * (1.0 + self.slippage_pct + self.half_spread_pct)

                if exit_price is not None:
                    exit_fee = (qty * exit_price) * self.fee_rate
                    raw_pnl = (
                        (exit_price - entry_price) * qty
                        if pos_side == "BUY"
                        else (entry_price - exit_price) * qty
                    )
                    pnl = raw_pnl - entry_fee - exit_fee
                    equity += pnl
                    ret_pct = pnl / max(1e-9, entry_price * qty)
                    trades.append(
                        TradeRecord(
                            pnl=pnl,
                            return_pct=ret_pct,
                            fees=entry_fee + exit_fee,
                            duration_bars=i - entry_bar,
                        )
                    )
                    in_pos = False
                    peak_equity = max(peak_equity, equity)
                    if peak_equity > 0 and (peak_equity - equity) / peak_equity >= self.max_drawdown_limit:
                        halted = True

            equity_curve.append(round(equity, 6))
            if halted:
                break

            # Strict zero-look-ahead slice: df.iloc[: i + 1]
            window_df = df.iloc[: i + 1]
            signal = strategy.generate_signal(symbol, window_df)

            if in_pos and ((pos_side == "BUY" and signal.action == "SELL") or (pos_side == "SELL" and signal.action == "BUY")):
                exec_p = (
                    float(next_bar["open"]) * (1.0 - self.slippage_pct - self.half_spread_pct)
                    if pos_side == "BUY"
                    else float(next_bar["open"]) * (1.0 + self.slippage_pct + self.half_spread_pct)
                )
                exit_fee = (qty * exec_p) * self.fee_rate
                raw_pnl = (
                    (exec_p - entry_price) * qty
                    if pos_side == "BUY"
                    else (entry_price - exec_p) * qty
                )
                pnl = raw_pnl - entry_fee - exit_fee
                equity += pnl
                ret_pct = pnl / max(1e-9, entry_price * qty)
                trades.append(
                    TradeRecord(
                        pnl=pnl,
                        return_pct=ret_pct,
                        fees=entry_fee + exit_fee,
                        duration_bars=(i + 1) - entry_bar,
                    )
                )
                in_pos = False
                peak_equity = max(peak_equity, equity)
                if peak_equity > 0 and (peak_equity - equity) / peak_equity >= self.max_drawdown_limit:
                    halted = True

            elif not in_pos and not halted and (signal.action == "BUY" or (allow_short and signal.action == "SELL")):
                is_long = signal.action == "BUY"
                exec_open = (
                    float(next_bar["open"]) * (1.0 + self.slippage_pct + self.half_spread_pct)
                    if is_long
                    else float(next_bar["open"]) * (1.0 - self.slippage_pct - self.half_spread_pct)
                )
                stop_dist_pct = abs(float(signal.entry_price) - float(signal.stop_loss)) / max(
                    1e-9, float(signal.entry_price)
                )
                if stop_dist_pct <= 0.001:
                    continue
                risk_budget = equity * self.risk_per_trade
                notional = min(risk_budget / stop_dist_pct, equity * 0.95)
                if notional <= 0 or exec_open <= 0:
                    continue
                qty = notional / exec_open
                pos_side = "BUY" if is_long else "SELL"
                entry_price = exec_open
                entry_fee = notional * self.fee_rate
                stop_loss = exec_open * (1.0 - stop_dist_pct) if is_long else exec_open * (1.0 + stop_dist_pct)
                tp_dist_pct = abs(float(signal.take_profit) - float(signal.entry_price)) / max(
                    1e-9, float(signal.entry_price)
                )
                take_profit = exec_open * (1.0 + tp_dist_pct) if is_long else max(exec_open * (1.0 - tp_dist_pct), exec_open * 0.05)
                entry_bar = i + 1
                in_pos = True

        # Close open position at final close for reporting
        if in_pos:
            final_p = (
                float(df.iloc[-1]["close"]) * (1.0 - self.slippage_pct - self.half_spread_pct)
                if pos_side == "BUY"
                else float(df.iloc[-1]["close"]) * (1.0 + self.slippage_pct + self.half_spread_pct)
            )
            exit_fee = (qty * final_p) * self.fee_rate
            raw_pnl = (
                (final_p - entry_price) * qty
                if pos_side == "BUY"
                else (entry_price - final_p) * qty
            )
            pnl = raw_pnl - entry_fee - exit_fee
            equity += pnl
            trades.append(
                TradeRecord(
                    pnl=pnl,
                    return_pct=pnl / max(1e-9, entry_price * qty),
                    fees=entry_fee + exit_fee,
                    duration_bars=len(df) - 1 - entry_bar,
                )
            )
            equity_curve.append(round(equity, 6))

        report = PerformanceMetricsCalculator.compute(
            trades=trades,
            equity_curve=equity_curve,
            initial_capital=self.initial_capital,
            result_type="BACKTEST_RESULTS",
        )
        return BacktestResult(
            strategy_name=strategy.name,
            symbol=symbol,
            report=report,
            trades=trades,
            equity_curve=equity_curve,
        )
