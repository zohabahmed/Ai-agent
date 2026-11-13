"""Strategy D: Volatility-Based Trading (ATR, compression-to-expansion breakout, unusual spike guard)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pandas as pd

from app.strategies.base_strategy import BaseStrategy, Indicators, StrategySignal


class VolatilityStrategy(BaseStrategy):
    name = "volatility_breakout"
    display_name = "Volatility Compression & Expansion"
    category = "volatility"
    preferred_regimes = ["VOLATILITY_CONTRACTION", "VOLATILITY_EXPANSION", "LOW_VOLATILITY"]
    min_candles = 60

    @classmethod
    def default_params(cls) -> dict[str, Any]:
        return {
            "bb_window": 20,
            "squeeze_percentile": 0.30,
            "expansion_ratio": 1.20,
            "extreme_atr_spike": 3.0,
            "atr_stop_mult": 1.5,
            "reward_risk_ratio": 2.2,
        }

    def generate_signal(self, symbol: str, df: pd.DataFrame) -> StrategySignal:
        if len(df) < self.min_candles:
            price = float(df["close"].iloc[-1]) if not df.empty else 0.0
            return self.hold_signal(self.name, symbol, price, "Insufficient candle history for volatility strategy.")

        close = df["close"].astype(float)
        high = df["high"].astype(float)
        low = df["low"].astype(float)
        upper, mid, lower = Indicators.bollinger_bands(close, int(self.params["bb_window"]), 2.0)
        bb_width = (upper - lower) / mid.replace(0.0, float("nan"))
        atr = Indicators.atr(df)
        atr_ma = Indicators.sma(atr, 20).replace(0.0, float("nan"))
        hist_vol = Indicators.historical_volatility(close, 20)

        curr_close = float(close.iloc[-1])
        curr_atr = float(atr.iloc[-1])
        curr_atr_ma = float(atr_ma.iloc[-1]) if pd.notna(atr_ma.iloc[-1]) else curr_atr
        atr_ratio = curr_atr / curr_atr_ma if curr_atr_ma > 0 else 1.0
        curr_bb_w = float(bb_width.iloc[-1]) if pd.notna(bb_width.iloc[-1]) else 0.0
        prev_bb_w = float(bb_width.iloc[-4]) if len(bb_width) > 4 and pd.notna(bb_width.iloc[-4]) else curr_bb_w
        bb_rank = float(bb_width.rank(pct=True).iloc[-2]) if len(bb_width) > 2 else 0.5
        curr_hv = float(hist_vol.iloc[-1]) if pd.notna(hist_vol.iloc[-1]) else 0.0
        recent_high = float(high.iloc[-10:-1].max())
        recent_low = float(low.iloc[-10:-1].min())

        metrics = {
            "atr": round(curr_atr, 6),
            "atr_ratio": round(atr_ratio, 3),
            "bb_width": round(curr_bb_w, 5),
            "bb_squeeze_rank": round(bb_rank, 3),
            "hist_vol": round(curr_hv, 5),
        }

        if curr_atr <= 0 or curr_close <= 0:
            return self.hold_signal(self.name, symbol, curr_close, "Invalid ATR or price.", metrics)

        # Reject abnormal/flash-crash volatility spikes
        if atr_ratio > float(self.params["extreme_atr_spike"]):
            return self.hold_signal(
                self.name,
                symbol,
                curr_close,
                f"Abnormal volatility spike detected (ATR ratio={atr_ratio:.2f}x). Protecting capital.",
                metrics,
            )

        was_squeezed = bb_rank <= float(self.params["squeeze_percentile"])
        expanding = curr_bb_w > prev_bb_w and atr_ratio >= float(self.params["expansion_ratio"])

        if was_squeezed and expanding and curr_close > recent_high:
            stop_dist = max(curr_atr * float(self.params["atr_stop_mult"]), curr_close * 0.005)
            conf = min(0.58 + min((atr_ratio - 1.0) * 0.15, 0.22), 0.83)
            return StrategySignal(
                strategy=self.name,
                symbol=symbol,
                action="BUY",
                confidence=round(conf, 4),
                entry_price=Decimal(str(round(curr_close, 6))),
                stop_loss=Decimal(str(round(curr_close - stop_dist, 6))),
                take_profit=Decimal(str(round(curr_close + stop_dist * float(self.params["reward_risk_ratio"]), 6))),
                reason=f"Volatility expansion breakout from compression (squeeze rank={bb_rank:.2f}, ATR ratio={atr_ratio:.2f}x).",
                indicators=metrics,
            )

        if was_squeezed and expanding and curr_close < recent_low:
            stop_dist = max(curr_atr * float(self.params["atr_stop_mult"]), curr_close * 0.005)
            return StrategySignal(
                strategy=self.name,
                symbol=symbol,
                action="SELL",
                confidence=0.65,
                entry_price=Decimal(str(round(curr_close, 6))),
                stop_loss=Decimal(str(round(curr_close + stop_dist, 6))),
                take_profit=Decimal(str(round(max(curr_close - stop_dist * 2.0, curr_close * 0.5), 6))),
                reason=f"Downside volatility expansion breakdown (ATR ratio={atr_ratio:.2f}x).",
                indicators=metrics,
            )

        # Also capture live volatility-expansion Donchian breakouts even without an extreme prior squeeze
        if atr_ratio >= 1.12 and len(df) >= 12:
            highs = df["high"].astype(float)
            lows = df["low"].astype(float)
            curr_atr_val = max(float(Indicators.atr(df).iloc[-1]), curr_close * 0.004)
            breakout_hi = float(highs.iloc[-9:-1].max())
            breakout_lo = float(lows.iloc[-9:-1].min())
            stop_dist_exp = max(curr_atr_val * 1.5, curr_close * 0.005)
            if curr_close > breakout_hi:
                return StrategySignal(
                    strategy=self.name,
                    symbol=symbol,
                    action="BUY",
                    confidence=round(min(0.60 + (atr_ratio - 1.12) * 0.25, 0.80), 4),
                    entry_price=Decimal(str(round(curr_close, 6))),
                    stop_loss=Decimal(str(round(curr_close - stop_dist_exp, 6))),
                    take_profit=Decimal(str(round(curr_close + stop_dist_exp * 2.1, 6))),
                    reason=f"ATR expansion 8-bar breakout up (ATR_ratio={atr_ratio:.2f}x).",
                    indicators=metrics,
                )
            if curr_close < breakout_lo:
                return StrategySignal(
                    strategy=self.name,
                    symbol=symbol,
                    action="SELL",
                    confidence=round(min(0.60 + (atr_ratio - 1.12) * 0.25, 0.80), 4),
                    entry_price=Decimal(str(round(curr_close, 6))),
                    stop_loss=Decimal(str(round(curr_close + stop_dist_exp, 6))),
                    take_profit=Decimal(str(round(max(curr_close - stop_dist_exp * 2.1, curr_close * 0.5), 6))),
                    reason=f"ATR expansion 8-bar breakdown down (ATR_ratio={atr_ratio:.2f}x).",
                    indicators=metrics,
                )

        return self.hold_signal(
            self.name,
            symbol,
            curr_close,
            f"No squeeze-to-expansion breakout (squeeze_rank={bb_rank:.2f}, ATR_ratio={atr_ratio:.2f}x).",
            metrics,
        )
