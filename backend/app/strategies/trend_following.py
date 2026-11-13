"""Strategy A: Trend Following (EMA20/EMA50, RSI, MACD, Volume confirmation, ADX trend strength)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pandas as pd

from app.strategies.base_strategy import BaseStrategy, Indicators, StrategySignal


class TrendFollowingStrategy(BaseStrategy):
    name = "trend_following"
    display_name = "Trend Following (EMA / MACD / ADX)"
    category = "trend"
    preferred_regimes = ["BULLISH_TRENDING", "BEARISH_TRENDING", "VOLATILITY_EXPANSION"]
    min_candles = 60

    @classmethod
    def default_params(cls) -> dict[str, Any]:
        return {
            "ema_fast": 20,
            "ema_slow": 50,
            "rsi_period": 14,
            "rsi_buy_min": 46.0,
            "rsi_buy_max": 82.0,
            "adx_min": 18.0,
            "volume_ratio_min": 0.90,
            "atr_stop_mult": 1.8,
            "reward_risk_ratio": 2.2,
        }

    def generate_signal(self, symbol: str, df: pd.DataFrame) -> StrategySignal:
        if len(df) < self.min_candles:
            price = float(df["close"].iloc[-1]) if not df.empty else 0.0
            return self.hold_signal(self.name, symbol, price, "Insufficient candle history for trend analysis.")

        close = df["close"].astype(float)
        volume = df["volume"].astype(float)
        ema_fast = Indicators.ema(close, int(self.params["ema_fast"]))
        ema_slow = Indicators.ema(close, int(self.params["ema_slow"]))
        rsi = Indicators.rsi(close, int(self.params["rsi_period"]))
        macd_line, signal_line, macd_hist = Indicators.macd(close)
        adx = Indicators.trend_strength_adx(df)
        atr = Indicators.atr(df)
        vol_sma = Indicators.sma(volume, 20).replace(0.0, float("nan"))

        curr_close = float(close.iloc[-1])
        curr_ema_f = float(ema_fast.iloc[-1])
        curr_ema_s = float(ema_slow.iloc[-1])
        prev_ema_f = float(ema_fast.iloc[-2])
        prev_ema_s = float(ema_slow.iloc[-2])
        curr_rsi = float(rsi.iloc[-1])
        curr_macd = float(macd_line.iloc[-1])
        curr_sig = float(signal_line.iloc[-1])
        curr_hist = float(macd_hist.iloc[-1])
        prev_hist = float(macd_hist.iloc[-2])
        curr_adx = float(adx.iloc[-1])
        curr_atr = float(atr.iloc[-1])
        vol_ratio = float(volume.iloc[-1] / vol_sma.iloc[-1]) if pd.notna(vol_sma.iloc[-1]) else 1.0

        metrics = {
            "ema_20": round(curr_ema_f, 6),
            "ema_50": round(curr_ema_s, 6),
            "rsi": round(curr_rsi, 2),
            "macd_hist": round(curr_hist, 6),
            "adx": round(curr_adx, 2),
            "atr": round(curr_atr, 6),
            "volume_ratio": round(vol_ratio, 2),
        }

        if curr_atr <= 0 or curr_close <= 0:
            return self.hold_signal(self.name, symbol, curr_close, "Zero ATR or price.", metrics)

        # Bullish trend or breakout with volume & ADX confirmation
        bullish_cross = prev_ema_f <= prev_ema_s and curr_ema_f > curr_ema_s
        bullish_aligned = curr_ema_f > curr_ema_s and curr_close > curr_ema_f
        macd_bullish = curr_macd > curr_sig and curr_hist >= prev_hist
        rsi_ok = self.params["rsi_buy_min"] <= curr_rsi <= self.params["rsi_buy_max"]
        trend_strong = curr_adx >= self.params["adx_min"]
        vol_confirmed = vol_ratio >= self.params["volume_ratio_min"]

        if (bullish_aligned or bullish_cross) and macd_bullish and rsi_ok and trend_strong and vol_confirmed:
            stop_dist = max(curr_atr * float(self.params["atr_stop_mult"]), curr_close * 0.005)
            stop_loss = Decimal(str(round(curr_close - stop_dist, 6)))
            take_profit = Decimal(str(round(curr_close + stop_dist * float(self.params["reward_risk_ratio"]), 6)))

            # Confidence scaled strictly by normalized indicator confluence (0.50 .. 0.85)
            adx_score = min((curr_adx - self.params["adx_min"]) / 30.0, 0.15)
            vol_score = min((vol_ratio - 1.0) / 2.0, 0.10)
            ema_spread = min(((curr_ema_f - curr_ema_s) / curr_ema_s) * 10.0, 0.10)
            conf = min(0.55 + max(0.0, adx_score) + max(0.0, vol_score) + max(0.0, ema_spread), 0.88)

            kind = "EMA crossover breakout" if bullish_cross else "Confirmed bullish trend continuation"
            return StrategySignal(
                strategy=self.name,
                symbol=symbol,
                action="BUY",
                confidence=round(conf, 4),
                entry_price=Decimal(str(round(curr_close, 6))),
                stop_loss=stop_loss,
                take_profit=take_profit,
                reason=f"{kind}: EMA20>{curr_ema_f:.2f}>EMA50({curr_ema_s:.2f}), ADX={curr_adx:.1f}, RSI={curr_rsi:.1f}, Vol={vol_ratio:.2f}x",
                indicators=metrics,
            )

        # Bearish reversal / short trend signal for Futures
        bearish_cross = prev_ema_f >= prev_ema_s and curr_ema_f < curr_ema_s
        bearish_aligned = curr_ema_f < curr_ema_s and curr_close < curr_ema_f and curr_macd < curr_sig and curr_hist <= prev_hist
        weakening_momentum = curr_close < curr_ema_f and curr_macd < curr_sig and curr_hist <= prev_hist and curr_rsi < 48.0
        if (bearish_cross or bearish_aligned or weakening_momentum) and trend_strong and vol_confirmed:
            stop_dist = max(curr_atr * float(self.params["atr_stop_mult"]), curr_close * 0.005)
            return StrategySignal(
                strategy=self.name,
                symbol=symbol,
                action="SELL",
                confidence=round(min(0.60 + min(curr_adx / 100.0, 0.20), 0.82), 4),
                entry_price=Decimal(str(round(curr_close, 6))),
                stop_loss=Decimal(str(round(curr_close + stop_dist, 6))),
                take_profit=Decimal(str(round(max(curr_close - stop_dist * 2.0, curr_close * 0.5), 6))),
                reason=f"Bearish trend / momentum breakdown: EMA20={curr_ema_f:.2f}, EMA50={curr_ema_s:.2f}, RSI={curr_rsi:.1f}",
                indicators=metrics,
            )

        return self.hold_signal(
            self.name,
            symbol,
            curr_close,
            f"No validated trend confluence (ADX={curr_adx:.1f}, RSI={curr_rsi:.1f}, Vol={vol_ratio:.2f}x).",
            metrics,
        )
