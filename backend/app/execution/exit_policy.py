"""Unified ATR-scaled stop/target normalization and scale-out + ATR trailing exit policy.

Used identically by BacktestEngine and AutonomousTradingLoop so historical validation,
EV calculation, and live execution follow the exact same exit rules.
"""

from __future__ import annotations

from dataclasses import dataclass, replace as dc_replace
from decimal import Decimal
from typing import Any

import pandas as pd

from app.config import Settings
from app.strategies.base_strategy import Indicators


@dataclass(frozen=True)
class ExitStepDecision:
    close_fraction: float  # 0.0 = hold, 0.5 = scale out 1R partial, 1.0 = close full remaining size
    exit_price: Decimal
    exit_reason: str  # STOP_LOSS, BREAKEVEN_LOCK, ATR_TRAILING_STOP, TAKE_PROFIT, SCALE_OUT_1R, TIME_STOP, or ""
    new_stop_loss: Decimal | None
    scaled_out: bool


def extract_atr_value(df: pd.DataFrame | None, period: int = 14) -> Decimal:
    """Compute latest ATR from OHLCV DataFrame, returning Decimal('0') if unavailable."""
    if df is None or len(df) < period + 2:
        return Decimal("0")
    try:
        atr_series = Indicators.atr(df, period)
        val = float(atr_series.iloc[-1])
        if pd.notna(val) and val > 0:
            return Decimal(str(round(val, 8)))
    except Exception:
        return Decimal("0")
    return Decimal("0")


def normalize_atr_targets(
    sig: Any,
    settings: Settings,
    df: pd.DataFrame | None = None,
) -> Any:
    """
    Preserve strategy ATR stop geometry within safe volatility bounds [min_stop_loss_pct, max_stop_loss_pct]
    (default 0.8%..2.5%) and enforce >= 2.0R take-profit distance.
    """
    entry = Decimal(str(sig.entry_price))
    if entry <= 0 or sig.action not in {"BUY", "SELL"}:
        return sig

    atr_val = extract_atr_value(df)
    if atr_val <= 0 and isinstance(getattr(sig, "indicators", None), dict):
        ind_atr = sig.indicators.get("atr")
        if ind_atr and float(ind_atr) > 0:
            atr_val = Decimal(str(ind_atr))

    raw_sl = Decimal(str(sig.stop_loss)) if sig.stop_loss and sig.stop_loss > 0 else Decimal("0")
    raw_tp = Decimal(str(sig.take_profit)) if sig.take_profit and sig.take_profit > 0 else Decimal("0")
    raw_sl_pct = abs(entry - raw_sl) / entry if raw_sl > 0 else Decimal("0")
    raw_tp_pct = abs(raw_tp - entry) / entry if raw_tp > 0 else Decimal("0")

    atr_floor_pct = (atr_val / entry) * Decimal("1.5") if atr_val > 0 else Decimal("0")
    base_stop_pct = max(raw_sl_pct, atr_floor_pct)
    if base_stop_pct <= 0:
        base_stop_pct = settings.quick_stop_loss_pct

    stop_pct = max(
        settings.min_stop_loss_pct,
        min(settings.max_stop_loss_pct, base_stop_pct),
    )
    min_tp_pct = max(settings.quick_take_profit_pct, stop_pct * settings.min_reward_risk_ratio)
    tp_pct = max(raw_tp_pct, min_tp_pct)

    quant = Decimal("0.000001") if entry >= Decimal("1") else Decimal("0.00000001")
    if sig.action == "SELL":
        sl = (entry * (Decimal("1") + stop_pct)).quantize(quant)
        tp = (entry * max(Decimal("0.05"), Decimal("1") - tp_pct)).quantize(quant)
    else:
        sl = (entry * max(Decimal("0.01"), Decimal("1") - stop_pct)).quantize(quant)
        tp = (entry * (Decimal("1") + tp_pct)).quantize(quant)

    indicators = dict(getattr(sig, "indicators", {}) or {})
    if atr_val > 0:
        indicators["atr"] = float(atr_val)
    indicators["initial_stop_distance_pct"] = float(stop_pct.quantize(Decimal("0.000001")))
    return dc_replace(sig, take_profit=tp, stop_loss=sl, indicators=indicators)


def clamp_quick_targets(sig: Any, settings: Settings, df: pd.DataFrame | None = None) -> Any:
    """Backwards-compatible alias delegating to ATR-scaled target normalization."""
    return normalize_atr_targets(sig, settings, df=df)


def evaluate_position_exit_step(
    *,
    side: str,
    strategy_name: str,
    entry_price: Decimal,
    current_price: Decimal,
    stop_loss: Decimal | None,
    take_profit: Decimal | None,
    initial_stop_distance_pct: Decimal | None,
    atr_value: Decimal | None,
    peak_price: Decimal,
    scaled_out: bool,
    held_minutes: float,
    settings: Settings,
    bar_high: Decimal | None = None,
    bar_low: Decimal | None = None,
    round_trip_fee_cushion_pct: Decimal = Decimal("0.0020"),
) -> ExitStepDecision:
    """
    Evaluate one bar or live polling step using unified stop, TP, 1R scale-out + breakeven,
    1.2x ATR runner trail, and strategy-appropriate max hold duration.
    """
    if entry_price <= 0 or current_price <= 0:
        return ExitStepDecision(0.0, current_price, "", stop_loss, scaled_out)

    is_short = side.upper() == "SHORT"
    hi = bar_high if bar_high is not None and bar_high > 0 else current_price
    lo = bar_low if bar_low is not None and bar_low > 0 else current_price

    # Resolve 1R risk distance fixed at entry
    r_unit = (
        initial_stop_distance_pct
        if initial_stop_distance_pct is not None and initial_stop_distance_pct > 0
        else (
            abs(entry_price - stop_loss) / entry_price
            if stop_loss is not None and stop_loss > 0 and stop_loss != entry_price
            else settings.quick_stop_loss_pct
        )
    )
    r_unit = max(Decimal("0.003"), min(Decimal("0.05"), r_unit))

    # 1. Conservative Stop-Loss check first (SL beats TP on ambiguous intrabar spikes)
    if stop_loss is not None and stop_loss > 0:
        be_threshold = (
            entry_price * (Decimal("1") - round_trip_fee_cushion_pct * Decimal("1.05"))
            if is_short
            else entry_price * (Decimal("1") + round_trip_fee_cushion_pct * Decimal("1.05"))
        )
        if is_short and hi >= stop_loss:
            if stop_loss < be_threshold and scaled_out:
                reason = "ATR_TRAILING_STOP"
            elif stop_loss < entry_price:
                reason = "BREAKEVEN_LOCK"
            else:
                reason = "STOP_LOSS"
            fill_px = current_price if bar_high is None else stop_loss
            return ExitStepDecision(1.0, fill_px, reason, stop_loss, scaled_out)
        if not is_short and lo <= stop_loss:
            if stop_loss > be_threshold and scaled_out:
                reason = "ATR_TRAILING_STOP"
            elif stop_loss > entry_price:
                reason = "BREAKEVEN_LOCK"
            else:
                reason = "STOP_LOSS"
            fill_px = current_price if bar_low is None else stop_loss
            return ExitStepDecision(1.0, fill_px, reason, stop_loss, scaled_out)

    # 2. Full Take-Profit check
    if take_profit is not None and take_profit > 0:
        if is_short and lo <= take_profit:
            fill_px = current_price if bar_low is None else take_profit
            return ExitStepDecision(1.0, fill_px, "TAKE_PROFIT", stop_loss, scaled_out)
        if not is_short and hi >= take_profit:
            fill_px = current_price if bar_high is None else take_profit
            return ExitStepDecision(1.0, fill_px, "TAKE_PROFIT", stop_loss, scaled_out)

    # Compute favorable peak move and current mark move relative to 1R
    best_peak = peak_price if peak_price > 0 else (min(current_price, lo) if is_short else max(current_price, hi))
    if is_short:
        curr_move = (entry_price - current_price) / entry_price
        peak_move = (entry_price - best_peak) / entry_price
        be_stop = entry_price * (Decimal("1") - round_trip_fee_cushion_pct)
    else:
        curr_move = (current_price - entry_price) / entry_price
        peak_move = (best_peak - entry_price) / entry_price
        be_stop = entry_price * (Decimal("1") + round_trip_fee_cushion_pct)

    one_r = r_unit * settings.scale_out_r_multiple
    atr_pct = (
        (atr_value / entry_price)
        if atr_value is not None and atr_value > 0
        else (r_unit / Decimal("1.5"))
    )
    trail_gap_pct = max(atr_pct * settings.atr_trail_multiplier, Decimal("0.006"))

    # Compute ratcheted stop level (never loosens)
    candidate_sl: Decimal | None = stop_loss
    quant = Decimal("0.000001") if entry_price >= Decimal("1") else Decimal("0.00000001")

    if not scaled_out and curr_move >= one_r:
        if candidate_sl is None:
            candidate_sl = be_stop.quantize(quant)
        elif is_short:
            candidate_sl = min(candidate_sl, be_stop.quantize(quant))
        else:
            candidate_sl = max(candidate_sl, be_stop.quantize(quant))
        return ExitStepDecision(
            close_fraction=float(settings.scale_out_fraction),
            exit_price=current_price,
            exit_reason="SCALE_OUT_1R",
            new_stop_loss=candidate_sl,
            scaled_out=True,
        )

    # Update 1.2x ATR runner trailing stop once scaled out or >= 1.2R favorable excursion reached
    if scaled_out or peak_move >= one_r * Decimal("1.2"):
        if is_short:
            raw_trail = best_peak * (Decimal("1") + trail_gap_pct)
            bounded_trail = min(be_stop, raw_trail).quantize(quant)
            if candidate_sl is None or bounded_trail < candidate_sl * Decimal("0.9998"):
                candidate_sl = bounded_trail
            if current_price >= candidate_sl and scaled_out:
                return ExitStepDecision(1.0, current_price, "ATR_TRAILING_STOP", candidate_sl, True)
        else:
            raw_trail = best_peak * (Decimal("1") - trail_gap_pct)
            bounded_trail = max(be_stop, raw_trail).quantize(quant)
            if candidate_sl is None or bounded_trail > candidate_sl * Decimal("1.0002"):
                candidate_sl = bounded_trail
            if current_price <= candidate_sl and scaled_out:
                return ExitStepDecision(1.0, current_price, "ATR_TRAILING_STOP", candidate_sl, True)

    # 3. Strategy-appropriate max hold expiry (no premature 30-min winner choke)
    max_hold = (
        settings.mean_reversion_max_hold_minutes
        if strategy_name == "mean_reversion"
        else settings.max_hold_minutes
    )
    if held_minutes >= max_hold:
        return ExitStepDecision(1.0, current_price, "TIME_STOP", candidate_sl, scaled_out)

    return ExitStepDecision(0.0, current_price, "", candidate_sl, scaled_out)
