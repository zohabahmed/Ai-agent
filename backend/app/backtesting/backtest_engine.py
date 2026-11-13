"""Zero look-ahead bar-by-bar Backtest Engine with realistic costs, slippage, and survival limits."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal
import pandas as pd

from app.backtesting.performance_metrics import (
    PerformanceMetricsCalculator,
    PerformanceReport,
    TradeRecord,
)
from app.config import Settings, get_settings
from app.execution.exit_policy import evaluate_position_exit_step, normalize_atr_targets
from app.strategies.base_strategy import BaseStrategy


@dataclass
class BacktestResult:
    strategy_name: str
    symbol: str
    report: PerformanceReport
    trades: list[TradeRecord]
    equity_curve: list[float]
    exit_reason_breakdown: dict[str, int] = field(default_factory=dict)


class BacktestEngine:
    """
    Reproducible, zero-look-ahead backtest engine aligned with live exit policy.
    - Signal evaluated strictly on closed bar i -> executed on bar i+1 open + spread/slippage.
    - Applies unified ATR-scaled SL/TP, 1R 50% scale-out + breakeven, 1.2x ATR trail, and max-hold expiry.
    - Deducts fees, spread, slippage, and 8h Futures funding costs.
    """

    def __init__(
        self,
        *,
        initial_capital: float = 100.0,
        fee_rate: float = 0.001,
        slippage_bps: float = 5.0,
        spread_bps: float = 4.0,
        funding_rate_8h: float = 0.0001,
        bar_minutes: int = 15,
        risk_per_trade: float = 0.015,
        max_drawdown_limit: float = 0.10,
        settings: Settings | None = None,
    ) -> None:
        self.initial_capital = initial_capital
        self.fee_rate = fee_rate
        self.slippage_pct = slippage_bps / 10000.0
        self.half_spread_pct = (spread_bps / 2.0) / 10000.0
        self.funding_rate_8h = funding_rate_8h
        self.bar_minutes = bar_minutes
        self.risk_per_trade = risk_per_trade
        self.max_drawdown_limit = max_drawdown_limit
        self.settings = settings or get_settings()

    def _apply_exit_slip(self, raw_px: float, pos_side: str) -> float:
        adj = self.slippage_pct + self.half_spread_pct
        return raw_px * (1.0 - adj) if pos_side == "BUY" else raw_px * (1.0 + adj)

    def run(
        self,
        strategy: BaseStrategy,
        symbol: str,
        df: pd.DataFrame,
        *,
        allow_short: bool = True,
        regime_label: str = "UNKNOWN",
    ) -> BacktestResult:
        equity = self.initial_capital
        peak_equity = self.initial_capital
        equity_curve: list[float] = [equity]
        trades: list[TradeRecord] = []

        in_pos = False
        pos_side = "BUY"
        qty = 0.0
        orig_notional = 0.0
        entry_price = 0.0
        entry_fee = 0.0
        stop_loss = Decimal("0")
        take_profit = Decimal("0")
        init_stop_dist_pct = Decimal("0.012")
        atr_val = Decimal("0")
        peak_price = Decimal("0")
        scaled_out = False
        partial_net_pnl = 0.0
        partial_fees = 0.0
        funding_accrued = 0.0
        entry_bar = 0
        halted = False

        start_idx = max(strategy.min_candles, 60)
        if len(df) <= start_idx + 1:
            rep = PerformanceMetricsCalculator.compute(
                trades=[], equity_curve=equity_curve, initial_capital=self.initial_capital
            )
            return BacktestResult(strategy.name, symbol, rep, [], equity_curve, {})

        for i in range(start_idx, len(df) - 1):
            bar = df.iloc[i]
            next_bar = df.iloc[i + 1]

            if in_pos:
                bar_close = float(bar["close"])
                bar_hi = Decimal(str(float(bar["high"])))
                bar_lo = Decimal(str(float(bar["low"])))
                bar_cl = Decimal(str(bar_close))
                peak_price = max(peak_price, bar_hi) if pos_side == "BUY" else min(peak_price, bar_lo)
                funding_accrued += (qty * bar_close) * self.funding_rate_8h * (self.bar_minutes / 480.0)

                decision = evaluate_position_exit_step(
                    side="LONG" if pos_side == "BUY" else "SHORT",
                    strategy_name=strategy.name,
                    entry_price=Decimal(str(entry_price)),
                    current_price=bar_cl,
                    bar_high=bar_hi,
                    bar_low=bar_lo,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    initial_stop_distance_pct=init_stop_dist_pct,
                    atr_value=atr_val,
                    peak_price=peak_price,
                    scaled_out=scaled_out,
                    held_minutes=(i - entry_bar) * self.bar_minutes,
                    settings=self.settings,
                )
                if decision.new_stop_loss is not None:
                    stop_loss = decision.new_stop_loss
                scaled_out = decision.scaled_out

                if 0.0 < decision.close_fraction < 1.0:
                    frac = decision.close_fraction
                    part_qty = qty * frac
                    exec_part = self._apply_exit_slip(float(decision.exit_price), pos_side)
                    part_exit_fee = (part_qty * exec_part) * self.fee_rate
                    part_entry_fee = entry_fee * frac
                    raw_part = (
                        (exec_part - entry_price) * part_qty
                        if pos_side == "BUY"
                        else (entry_price - exec_part) * part_qty
                    )
                    net_part = raw_part - part_entry_fee - part_exit_fee
                    equity += net_part
                    partial_net_pnl += net_part
                    partial_fees += part_entry_fee + part_exit_fee
                    qty -= part_qty
                    entry_fee -= part_entry_fee
                elif decision.close_fraction >= 1.0:
                    exec_p = self._apply_exit_slip(float(decision.exit_price), pos_side)
                    exit_fee = (qty * exec_p) * self.fee_rate
                    raw_rem = (
                        (exec_p - entry_price) * qty
                        if pos_side == "BUY"
                        else (entry_price - exec_p) * qty
                    )
                    net_rem = raw_rem - entry_fee - exit_fee - funding_accrued
                    equity += net_rem
                    total_pnl = partial_net_pnl + net_rem
                    total_fees = partial_fees + entry_fee + exit_fee + funding_accrued
                    ret_pct = total_pnl / max(1e-9, orig_notional)
                    trades.append(
                        TradeRecord(
                            pnl=total_pnl,
                            return_pct=ret_pct,
                            fees=total_fees,
                            duration_bars=max(1, i - entry_bar),
                            regime=regime_label,
                            exit_reason=decision.exit_reason or "CLOSED",
                        )
                    )
                    in_pos = False
                    peak_equity = max(peak_equity, equity)
                    if peak_equity > 0 and (peak_equity - equity) / peak_equity >= self.max_drawdown_limit:
                        halted = True

            equity_curve.append(round(equity, 6))
            if halted:
                break

            if not in_pos:
                # Strict zero-look-ahead slice: df.iloc[: i + 1]
                window_df = df.iloc[: i + 1]
                raw_signal = strategy.generate_signal(symbol, window_df)
                if raw_signal.action == "BUY" or (allow_short and raw_signal.action == "SELL"):
                    signal = normalize_atr_targets(raw_signal, self.settings, df=window_df)
                    is_long = signal.action == "BUY"
                    exec_open = (
                        float(next_bar["open"]) * (1.0 + self.slippage_pct + self.half_spread_pct)
                        if is_long
                        else float(next_bar["open"]) * (1.0 - self.slippage_pct - self.half_spread_pct)
                    )
                    stop_dist_pct = abs(float(signal.entry_price) - float(signal.stop_loss)) / max(
                        1e-9, float(signal.entry_price)
                    )
                    if stop_dist_pct <= 0.001 or exec_open <= 0:
                        continue
                    risk_budget = equity * self.risk_per_trade
                    notional = min(risk_budget / stop_dist_pct, equity * 0.40 if equity >= 20.0 else equity * 0.95)
                    if notional <= 0:
                        continue
                    qty = notional / exec_open
                    orig_notional = notional
                    pos_side = "BUY" if is_long else "SELL"
                    entry_price = exec_open
                    entry_fee = notional * self.fee_rate
                    init_stop_dist_pct = Decimal(str(round(stop_dist_pct, 6)))
                    atr_val = Decimal(str(signal.indicators.get("atr") or 0))
                    stop_loss = (
                        Decimal(str(exec_open)) * (Decimal("1") - init_stop_dist_pct)
                        if is_long
                        else Decimal(str(exec_open)) * (Decimal("1") + init_stop_dist_pct)
                    )
                    tp_dist_pct = abs(float(signal.take_profit) - float(signal.entry_price)) / max(
                        1e-9, float(signal.entry_price)
                    )
                    take_profit = (
                        Decimal(str(exec_open)) * (Decimal("1") + Decimal(str(tp_dist_pct)))
                        if is_long
                        else max(Decimal(str(exec_open)) * (Decimal("1") - Decimal(str(tp_dist_pct))), Decimal(str(exec_open * 0.05)))
                    )
                    peak_price = Decimal(str(exec_open))
                    scaled_out = False
                    partial_net_pnl = 0.0
                    partial_fees = 0.0
                    funding_accrued = 0.0
                    entry_bar = i + 1
                    in_pos = True

        # Close open position at final close for reporting
        if in_pos:
            final_p = self._apply_exit_slip(float(df.iloc[-1]["close"]), pos_side)
            exit_fee = (qty * final_p) * self.fee_rate
            raw_rem = (
                (final_p - entry_price) * qty
                if pos_side == "BUY"
                else (entry_price - final_p) * qty
            )
            net_rem = raw_rem - entry_fee - exit_fee - funding_accrued
            equity += net_rem
            total_pnl = partial_net_pnl + net_rem
            total_fees = partial_fees + entry_fee + exit_fee + funding_accrued
            trades.append(
                TradeRecord(
                    pnl=total_pnl,
                    return_pct=total_pnl / max(1e-9, orig_notional),
                    fees=total_fees,
                    duration_bars=max(1, len(df) - 1 - entry_bar),
                    regime=regime_label,
                    exit_reason="END_OF_WINDOW",
                )
            )
            equity_curve.append(round(equity, 6))

        report = PerformanceMetricsCalculator.compute(
            trades=trades,
            equity_curve=equity_curve,
            initial_capital=self.initial_capital,
            result_type="BACKTEST_RESULTS",
        )
        breakdown = dict(Counter(t.exit_reason for t in trades))
        return BacktestResult(
            strategy_name=strategy.name,
            symbol=symbol,
            report=report,
            trades=trades,
            equity_curve=equity_curve,
            exit_reason_breakdown=breakdown,
        )
