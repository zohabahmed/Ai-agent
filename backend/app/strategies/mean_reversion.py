"""Strategy B: Mean Reversion (RSI, Bollinger Bands, MA deviation, volatility-gated)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pandas as pd

from app.strategies.base_strategy import BaseStrategy, Indicators, StrategySignal


class MeanReversionStrategy(BaseStrategy):
    name = "mean_reversion"
    display_name = "Mean Reversion (Bollinger / Z-Score / RSI)"
    category = "mean_reversion"
    preferred_regimes = ["SIDEWAYS", "LOW_VOLATILITY", "VOLATILITY_CONTRACTION"]
    min_candles = 60

    @classmethod
    def default_params(cls) -> dict[str, Any]:
        return {
            "bb_window": 20,
            "bb_std": 2.0,
            "rsi_period": 14,
            "rsi_oversold": 36.0,
            "rsi_overbought": 64.0,
            "zscore_entry": -1.35,
            "zscore_exit": 1.35,
            "max_hist_vol": 0.05,
            "atr_stop_mult": 1.5,
        }

    def generate_signal(self, symbol: str, df: pd.DataFrame) -> StrategySignal:
        if len(df) < self.min_candles:
            price = float(df["close"].iloc[-1]) if not df.empty else 0.0
            return self.hold_signal(self.name, symbol, price, "Insufficient candle history for mean reversion.")

        close = df["close"].astype(float)
        bb_win = int(self.params["bb_window"])
        upper, mid, lower = Indicators.bollinger_bands(close, bb_win, float(self.params["bb_std"]))
        std = close.rolling(window=bb_win, min_periods=bb_win).std(ddof=0).replace(0.0, float("nan"))
        zscore = (close - mid) / std
        rsi = Indicators.rsi(close, int(self.params["rsi_period"]))
        hist_vol = Indicators.historical_volatility(close, 20)
        atr = Indicators.atr(df)

        curr_close = float(close.iloc[-1])
        prev_close = float(close.iloc[-2])
        curr_mid = float(mid.iloc[-1])
        curr_lower = float(lower.iloc[-1])
        curr_upper = float(upper.iloc[-1])
        curr_z = float(zscore.iloc[-1]) if pd.notna(zscore.iloc[-1]) else 0.0
        curr_rsi = float(rsi.iloc[-1])
        prev_rsi = float(rsi.iloc[-2])
        curr_vol = float(hist_vol.iloc[-1]) if pd.notna(hist_vol.iloc[-1]) else 0.0
        curr_atr = float(atr.iloc[-1])

        metrics = {
            "bb_mid": round(curr_mid, 6),
            "bb_lower": round(curr_lower, 6),
            "bb_upper": round(curr_upper, 6),
            "zscore": round(curr_z, 3),
            "rsi": round(curr_rsi, 2),
            "hist_vol": round(curr_vol, 5),
            "atr": round(curr_atr, 6),
        }

        if curr_atr <= 0 or curr_close <= 0:
            return self.hold_signal(self.name, symbol, curr_close, "Invalid price/ATR.", metrics)

        # Do not buy oversold assets during extreme volatility crashes
        if curr_vol > float(self.params["max_hist_vol"]):
            return self.hold_signal(
                self.name,
                symbol,
                curr_close,
                f"Volatility {curr_vol:.4f} exceeds safe mean-reversion ceiling {self.params['max_hist_vol']}.",
                metrics,
            )

        # Require stabilization (RSI turning up or candle bouncing off lower band) rather than blind catching
        stabilizing = curr_close >= prev_close or curr_rsi > prev_rsi
        if (
            (curr_close <= curr_lower * 1.008 or curr_z <= float(self.params["zscore_entry"]))
            and curr_z <= float(self.params["zscore_entry"])
            and curr_rsi <= float(self.params["rsi_oversold"])
            and stabilizing
        ):
            stop_dist = max(curr_atr * float(self.params["atr_stop_mult"]), curr_close * 0.005)
            take_profit_price = max(curr_mid, curr_close + stop_dist * 1.8)
            conf = min(0.55 + min(abs(curr_z) - 1.5, 1.5) * 0.12 + max(0.0, (35.0 - curr_rsi) / 100.0), 0.84)
            return StrategySignal(
                strategy=self.name,
                symbol=symbol,
                action="BUY",
                confidence=round(conf, 4),
                entry_price=Decimal(str(round(curr_close, 6))),
                stop_loss=Decimal(str(round(curr_close - stop_dist, 6))),
                take_profit=Decimal(str(round(take_profit_price, 6))),
                reason=f"Statistically oversold stabilization: Z={curr_z:.2f}, RSI={curr_rsi:.1f}, price near lower BB ({curr_lower:.2f})",
                indicators=metrics,
            )

        if curr_close >= curr_upper * 0.997 and curr_z >= float(self.params["zscore_exit"]) and curr_rsi >= float(self.params["rsi_overbought"]):
            stop_dist = max(curr_atr * float(self.params["atr_stop_mult"]), curr_close * 0.005)
            return StrategySignal(
                strategy=self.name,
                symbol=symbol,
                action="SELL",
                confidence=round(min(0.58 + min(curr_z - 1.5, 1.5) * 0.12, 0.82), 4),
                entry_price=Decimal(str(round(curr_close, 6))),
                stop_loss=Decimal(str(round(curr_close + stop_dist, 6))),
                take_profit=Decimal(str(round(curr_mid, 6))),
                reason=f"Overbought mean-reversion exit: Z={curr_z:.2f}, RSI={curr_rsi:.1f}, near upper BB ({curr_upper:.2f})",
                indicators=metrics,
            )

        return self.hold_signal(
            self.name,
            symbol,
            curr_close,
            f"Price within normal statistical band (Z={curr_z:.2f}, RSI={curr_rsi:.1f}).",
            metrics,
        )
