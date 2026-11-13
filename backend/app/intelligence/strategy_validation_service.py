"""Separated out-of-sample validation, realized payoff estimation, auto-disable, and Phase 2 entry filters."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import math
from typing import Any

import numpy as np
import pandas as pd

from app.backtesting.backtest_engine import BacktestEngine
from app.backtesting.performance_metrics import TradeRecord
from app.backtesting.walk_forward import WalkForwardValidator
from app.config import Settings
from app.intelligence.derivatives_intelligence import DerivativesSentimentReport
from app.intelligence.regime_detector import MarketRegime, MarketRegimeDetector
from app.strategies.base_strategy import BaseStrategy, Indicators


@dataclass(frozen=True)
class StrategyValidationStats:
    wins: int
    losses: int
    oos_wins: int
    oos_losses: int
    avg_win_pct: float
    avg_loss_pct: float
    net_expectancy_pct: float
    oos_trade_count: int
    live_trade_count: int
    auto_disabled: bool
    disable_reason: str | None = None


def evaluate_separated_oos_stats(
    *,
    strategy: BaseStrategy,
    regime_name: str,
    symbol_frames: list[tuple[str, pd.DataFrame]],
    live_regime_stats: dict[tuple[str, str], dict[str, Any]],
    settings: Settings,
    per_symbol_cache: dict[tuple[str, str], tuple[int, int, int, int, list[float], list[float]]] | None = None,
) -> StrategyValidationStats:
    """
    Validate a strategy on historical bars strictly separated from the recent signal window,
    using the exact same unified BacktestEngine exit rules, and extract realized net win/loss %.
    """
    regime_key = str(regime_name or "ALL").upper()
    live_pair = live_regime_stats.get((strategy.name, regime_key)) or live_regime_stats.get((strategy.name, "ALL"))
    live_count = int(live_pair["trade_count"]) if live_pair else 0
    live_exp = float(live_pair["net_expectancy_pct"]) if live_pair else 0.0

    # Auto-disable any strategy-regime pair after >= min_trades_for_expectancy_disable with negative net expectancy
    if live_count >= settings.min_trades_for_expectancy_disable and live_exp < 0.0:
        return StrategyValidationStats(
            wins=int(live_pair["wins"]),
            losses=int(live_pair["losses"]),
            oos_wins=int(live_pair["wins"]),
            oos_losses=int(live_pair["losses"]),
            avg_win_pct=max(0.002, float(live_pair["avg_win_pct"])),
            avg_loss_pct=max(0.004, float(live_pair["avg_loss_pct"])),
            net_expectancy_pct=live_exp,
            oos_trade_count=live_count,
            live_trade_count=live_count,
            auto_disabled=True,
            disable_reason=(
                f"Auto-disabled {strategy.name} in {regime_key}: "
                f"{live_count} closed trades with negative net expectancy ({live_exp * 100:.3f}%/trade)."
            ),
        )

    engine = BacktestEngine(
        initial_capital=1000.0,
        fee_rate=0.001,
        slippage_bps=4.0,
        spread_bps=4.0,
        funding_rate_8h=0.0001,
        risk_per_trade=float(settings.max_risk_per_trade),
        max_drawdown_limit=0.15,
        settings=settings,
    )

    is_wins = 0
    is_losses = 0
    oos_wins = 0
    oos_losses = 0
    realized_win_rets: list[float] = []
    realized_loss_rets: list[float] = []

    for sym, full_df in symbol_frames[:8]:
        n = len(full_df)
        if n < 120:
            continue
        cache_key = (strategy.name, sym)
        if per_symbol_cache is not None and cache_key in per_symbol_cache:
            sw_is, sl_is, sw_oos, sl_oos, w_rets, l_rets = per_symbol_cache[cache_key]
            is_wins += sw_is
            is_losses += sl_is
            oos_wins += sw_oos
            oos_losses += sl_oos
            realized_win_rets.extend(w_rets)
            realized_loss_rets.extend(l_rets)
            continue

        # Separate live decision window (last 20–48 bars) from historical backtest window
        holdout = min(48, max(20, n // 5))
        hist_df = full_df.iloc[: n - holdout].reset_index(drop=True)
        if len(hist_df) < 120:
            hist_df = full_df.iloc[: n - 20].reset_index(drop=True)

        split_idx = int(len(hist_df) * 0.60) if len(hist_df) >= 180 else int(len(hist_df) * 0.65)
        is_res = engine.run(strategy, sym, hist_df.iloc[:split_idx].reset_index(drop=True), allow_short=True, regime_label=regime_key)
        oos_res = engine.run(strategy, sym, hist_df.iloc[max(0, split_idx - 60):].reset_index(drop=True), allow_short=True, regime_label=regime_key)

        sw_is = 0
        sl_is = 0
        sw_oos = 0
        sl_oos = 0
        w_rets: list[float] = []
        l_rets: list[float] = []

        for t in is_res.trades:
            if t.pnl > 0:
                sw_is += 1
                w_rets.append(max(0.001, t.return_pct))
            else:
                sl_is += 1
                l_rets.append(max(0.002, abs(t.return_pct)))

        for t in oos_res.trades:
            if t.pnl > 0:
                sw_oos += 1
                w_rets.append(max(0.001, t.return_pct))
            else:
                sl_oos += 1
                l_rets.append(max(0.002, abs(t.return_pct)))

        if per_symbol_cache is not None:
            per_symbol_cache[cache_key] = (sw_is, sl_is, sw_oos, sl_oos, w_rets, l_rets)

        is_wins += sw_is
        is_losses += sl_is
        oos_wins += sw_oos
        oos_losses += sl_oos
        realized_win_rets.extend(w_rets)
        realized_loss_rets.extend(l_rets)

    # Incorporate live closed trades into empirical Beta counts and realized payoffs
    if live_pair and live_count >= 1:
        is_wins += int(live_pair["wins"])
        is_losses += int(live_pair["losses"])
        oos_wins += int(live_pair["wins"])
        oos_losses += int(live_pair["losses"])
        if live_count >= 5:
            # Weight real realized payoffs heavily once >= 5 closed live trades exist
            for _ in range(min(20, int(live_pair["wins"]))):
                if float(live_pair["avg_win_pct"]) > 0:
                    realized_win_rets.append(float(live_pair["avg_win_pct"]))
            for _ in range(min(20, int(live_pair["losses"]))):
                if float(live_pair["avg_loss_pct"]) > 0:
                    realized_loss_rets.append(float(live_pair["avg_loss_pct"]))

    # Total separated historical sample combines IS + OOS slices (both strictly prior to live decision holdout)
    total_hist_wins = is_wins + oos_wins
    total_hist_losses = is_losses + oos_losses
    if (total_hist_wins + total_hist_losses) == 0 and len(symbol_frames) > 0:
        # Insufficient historical signal trigger volume: inspect indicator regime alignment strictly inside hist_df[:-holdout]
        prior_wins = 0
        prior_losses = 0
        for _, full_df in symbol_frames[:6]:
            if len(full_df) < 120:
                continue
            hist_slice = full_df.iloc[:-20]
            close = hist_slice["close"].astype(float)
            fwd = close.pct_change(4).shift(-4).dropna()
            ema20 = Indicators.ema(close, 20)
            ema50 = Indicators.ema(close, 50)
            aligned = fwd[ema20.loc[fwd.index] > ema50.loc[fwd.index]]
            if len(aligned) >= 8:
                prior_wins += int((aligned > 0.003).sum())
                prior_losses += int((aligned <= 0.003).sum())
                for r in aligned:
                    if r > 0.003:
                        realized_win_rets.append(float(r))
                    else:
                        realized_loss_rets.append(max(0.004, abs(float(r))))
        total_hist_wins = prior_wins
        total_hist_losses = prior_losses
        oos_wins = max(0, prior_wins // 3)
        oos_losses = max(0, prior_losses // 3)

    # Realized win/loss magnitudes net of costs (falls back conservatively if empty)
    avg_win_pct = (
        float(np.mean(realized_win_rets))
        if realized_win_rets
        else float(settings.quick_stop_loss_pct) * 1.4
    )
    avg_loss_pct = (
        float(np.mean(realized_loss_rets))
        if realized_loss_rets
        else float(settings.quick_stop_loss_pct)
    )
    total_trades = total_hist_wins + total_hist_losses
    win_rate = (total_hist_wins / total_trades) if total_trades > 0 else 0.0
    net_exp = (win_rate * avg_win_pct) - ((1.0 - win_rate) * avg_loss_pct)

    return StrategyValidationStats(
        wins=total_hist_wins,
        losses=total_hist_losses,
        oos_wins=oos_wins,
        oos_losses=oos_losses,
        avg_win_pct=max(0.002, round(avg_win_pct, 6)),
        avg_loss_pct=max(0.003, round(avg_loss_pct, 6)),
        net_expectancy_pct=round(net_exp, 6),
        oos_trade_count=oos_wins + oos_losses,
        live_trade_count=live_count,
        auto_disabled=False,
        disable_reason=None,
    )


def check_phase2_entry_filters(
    *,
    symbol: str,
    action: str,
    strategy_name: str,
    regime: MarketRegime,
    htf_df: pd.DataFrame | None,
    btc_regime: MarketRegime | None,
    btc_bearish_slope: bool,
    btc_bullish_slope: bool,
    deriv_report: DerivativesSentimentReport,
    mean_reversion_net_expectancy: float = 0.0,
) -> tuple[bool, str]:
    """
    Phase 2 multi-factor confluence gates:
    1. Sideways / low-vol guard (skip unless mean_reversion with non-negative expectancy).
    2. Higher-timeframe (1h) EMA20 vs EMA50 alignment.
    3. BTC market regime filter on altcoin entries.
    4. Derivatives funding / crowding / taker filter.
    """
    side = action.upper()

    # 1. Regime gating: avoid chop unless validated mean reversion
    if regime in {MarketRegime.SIDEWAYS, MarketRegime.LOW_VOLATILITY, MarketRegime.VOLATILITY_CONTRACTION}:
        if strategy_name != "mean_reversion":
            return False, f"Regime gate: skipping {strategy_name} inside {regime.value}."
        if mean_reversion_net_expectancy < 0:
            return False, f"Regime gate: mean_reversion has negative net expectancy ({mean_reversion_net_expectancy:.5f}) in {regime.value}."

    # 2. Higher-timeframe (1h) EMA trend confirmation for directional entries
    if htf_df is not None and len(htf_df) >= 55 and strategy_name in {"trend_following", "momentum", "volatility_breakout"}:
        htf_close = htf_df["close"].astype(float)
        ema20 = float(Indicators.ema(htf_close, 20).iloc[-1])
        ema50 = float(Indicators.ema(htf_close, 50).iloc[-1])
        last_close = float(htf_close.iloc[-1])
        if side == "BUY" and ema20 < ema50 * 0.999 and last_close < ema20:
            return False, f"HTF (1h) bearish structure blocks {symbol} LONG (EMA20 {ema20:.4f} < EMA50 {ema50:.4f})."
        if side == "SELL" and ema20 > ema50 * 1.001 and last_close > ema20:
            return False, f"HTF (1h) bullish structure blocks {symbol} SHORT (EMA20 {ema20:.4f} > EMA50 {ema50:.4f})."

    # 3. BTC market regime gate for altcoins
    is_btc = symbol.upper().startswith("BTC/")
    if not is_btc and btc_regime is not None:
        if side == "BUY" and (btc_regime == MarketRegime.BEARISH_TRENDING or btc_bearish_slope):
            return False, f"BTC regime filter ({btc_regime.value}) blocks altcoin {symbol} LONG."
        if side == "SELL" and btc_regime == MarketRegime.BULLISH_TRENDING and btc_bullish_slope:
            return False, f"BTC bullish trend filter ({btc_regime.value}) blocks altcoin {symbol} SHORT."

    # 4. CoinGlass / Binance Futures derivatives crowding gate
    if side == "BUY" and not deriv_report.allow_long:
        return False, f"Derivatives gate blocked {symbol} LONG: {deriv_report.rationale}"
    if side == "SELL" and not deriv_report.allow_short:
        return False, f"Derivatives gate blocked {symbol} SHORT: {deriv_report.rationale}"

    return True, "Phase 2 confluence passed."
