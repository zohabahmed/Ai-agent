"""Strategy C: Momentum (Price ROC, relative strength, volume expansion, persistence & acceleration)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pandas as pd

from app.strategies.base_strategy import BaseStrategy, Indicators, StrategySignal


class MomentumStrategy(BaseStrategy):
    name = "momentum"
    display_name = "Momentum Persistence & Volume Expansion"
    category = "momentum"
    preferred_regimes = ["BULLISH_TRENDING", "VOLATILITY_EXPANSION"]
    min_candles = 60

    @classmethod
    def default_params(cls) -> dict[str, Any]:
        return {
            "roc_fast": 5,
            "roc_slow": 15,
            "roc_threshold": 0.0045,
            "volume_expansion_min": 1.05,
            "persistence_bars": 3,
            "atr_stop_mult": 1.6,
            "reward_risk_ratio": 2.0,
        }

    def generate_signal(self, symbol: str, df: pd.DataFrame) -> StrategySignal:
        if len(df) < self.min_candles:
            price = float(df["close"].iloc[-1]) if not df.empty else 0.0
            return self.hold_signal(self.name, symbol, price, "Insufficient candle history for momentum.")

        close = df["close"].astype(float)
        volume = df["volume"].astype(float)
        roc_fast_n = int(self.params["roc_fast"])
        roc_slow_n = int(self.params["roc_slow"])

        roc_fast = close.pct_change(roc_fast_n)
        roc_slow = close.pct_change(roc_slow_n)
        roc_accel = roc_fast - roc_fast.shift(2)
        rsi = Indicators.rsi(close, 14)
        atr = Indicators.atr(df)
        vol_sma = Indicators.sma(volume, 20).replace(0.0, float("nan"))

        curr_close = float(close.iloc[-1])
        curr_roc_f = float(roc_fast.iloc[-1]) if pd.notna(roc_fast.iloc[-1]) else 0.0
        curr_roc_s = float(roc_slow.iloc[-1]) if pd.notna(roc_slow.iloc[-1]) else 0.0
        curr_accel = float(roc_accel.iloc[-1]) if pd.notna(roc_accel.iloc[-1]) else 0.0
        curr_rsi = float(rsi.iloc[-1])
        curr_atr = float(atr.iloc[-1])
        vol_ratio = float(volume.iloc[-1] / vol_sma.iloc[-1]) if pd.notna(vol_sma.iloc[-1]) else 1.0

        # Persistence: count positive closes in recent N bars
        n_bars = int(self.params["persistence_bars"])
        recent_rets = close.pct_change().iloc[-n_bars:]
        pos_bars = int((recent_rets > 0).sum())
        neg_bars = int((recent_rets < 0).sum())

        metrics = {
            "roc_fast": round(curr_roc_f, 5),
            "roc_slow": round(curr_roc_s, 5),
            "roc_accel": round(curr_accel, 5),
            "rsi": round(curr_rsi, 2),
            "volume_ratio": round(vol_ratio, 2),
            "pos_bars": float(pos_bars),
        }

        if curr_atr <= 0 or curr_close <= 0:
            return self.hold_signal(self.name, symbol, curr_close, "Invalid price/ATR.", metrics)

        thresh = float(self.params["roc_threshold"])
        if (
            curr_roc_f >= thresh
            and curr_roc_s > 0
            and curr_accel >= -0.0005
            and pos_bars >= max(2, n_bars - 1)
            and vol_ratio >= float(self.params["volume_expansion_min"])
            and 48.0 <= curr_rsi <= 84.0
        ):
            stop_dist = max(curr_atr * float(self.params["atr_stop_mult"]), curr_close * 0.005)
            conf = min(0.56 + min(curr_roc_f * 8.0, 0.16) + min((vol_ratio - 1.0) * 0.10, 0.12), 0.85)
            return StrategySignal(
                strategy=self.name,
                symbol=symbol,
                action="BUY",
                confidence=round(conf, 4),
                entry_price=Decimal(str(round(curr_close, 6))),
                stop_loss=Decimal(str(round(curr_close - stop_dist, 6))),
                take_profit=Decimal(str(round(curr_close + stop_dist * float(self.params["reward_risk_ratio"]), 6))),
                reason=f"Positive momentum acceleration: ROC_fast={curr_roc_f:.2%}, ROC_slow={curr_roc_s:.2%}, Vol={vol_ratio:.2f}x",
                indicators=metrics,
            )

        if curr_roc_f <= -thresh and curr_roc_s < 0 and neg_bars >= max(2, n_bars - 1) and curr_rsi < 45.0:
            stop_dist = max(curr_atr * float(self.params["atr_stop_mult"]), curr_close * 0.005)
            return StrategySignal(
                strategy=self.name,
                symbol=symbol,
                action="SELL",
                confidence=round(min(0.58 + min(abs(curr_roc_f) * 8.0, 0.20), 0.82), 4),
                entry_price=Decimal(str(round(curr_close, 6))),
                stop_loss=Decimal(str(round(curr_close + stop_dist, 6))),
                take_profit=Decimal(str(round(max(curr_close - stop_dist * 1.8, curr_close * 0.5), 6))),
                reason=f"Negative momentum breakdown: ROC_fast={curr_roc_f:.2%}, RSI={curr_rsi:.1f}",
                indicators=metrics,
            )

        return self.hold_signal(
            self.name,
            symbol,
            curr_close,
            f"No validated momentum expansion (ROC={curr_roc_f:.2%}, Vol={vol_ratio:.2f}x).",
            metrics,
        )
