"""Market Regime Detection Engine classifying into 8 regimes + UNKNOWN with confidence tracking."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

import numpy as np
import pandas as pd

from app.strategies.base_strategy import Indicators


class MarketRegime(str, Enum):
    BULLISH_TRENDING = "BULLISH_TRENDING"
    BEARISH_TRENDING = "BEARISH_TRENDING"
    SIDEWAYS = "SIDEWAYS"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"
    LOW_VOLATILITY = "LOW_VOLATILITY"
    VOLATILITY_EXPANSION = "VOLATILITY_EXPANSION"
    VOLATILITY_CONTRACTION = "VOLATILITY_CONTRACTION"
    ABNORMAL_UNSTABLE = "ABNORMAL_UNSTABLE"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class RegimeAssessment:
    primary_regime: MarketRegime
    secondary_regimes: list[MarketRegime]
    confidence: float
    volatility_pct: float
    trend_strength_adx: float
    trend_slope: float
    reason: str
    metrics: dict[str, float] = field(default_factory=dict)

    @property
    def is_abnormal(self) -> bool:
        return self.primary_regime == MarketRegime.ABNORMAL_UNSTABLE

    @property
    def is_uncertain(self) -> bool:
        return self.primary_regime == MarketRegime.UNKNOWN or self.confidence < 0.45


class MarketRegimeDetector:
    """Multi-indicator statistical market regime detector."""

    def detect(self, df: pd.DataFrame) -> RegimeAssessment:
        if df.empty or len(df) < 50:
            return RegimeAssessment(
                primary_regime=MarketRegime.UNKNOWN,
                secondary_regimes=[],
                confidence=0.0,
                volatility_pct=0.0,
                trend_strength_adx=0.0,
                trend_slope=0.0,
                reason="Insufficient candles (<50) to classify market regime.",
            )

        close = df["close"].astype(float)
        ema_20 = Indicators.ema(close, 20)
        ema_50 = Indicators.ema(close, 50)
        adx = Indicators.trend_strength_adx(df, 14)
        atr = Indicators.atr(df, 14)
        hv = Indicators.historical_volatility(close, 20)

        curr_close = float(close.iloc[-1])
        curr_ema20 = float(ema_20.iloc[-1])
        curr_ema50 = float(ema_50.iloc[-1])
        curr_adx = float(adx.iloc[-1])
        curr_atr = float(atr.iloc[-1])
        curr_hv = float(hv.iloc[-1]) if pd.notna(hv.iloc[-1]) else 0.0

        # Rolling volatility baseline
        hv_median = float(hv.dropna().median()) if not hv.dropna().empty else curr_hv
        atr_pct = (curr_atr / curr_close) if curr_close > 0 else 0.0
        atr_series_pct = (atr / close).dropna()
        atr_z = 0.0
        if len(atr_series_pct) >= 20 and float(atr_series_pct.std()) > 1e-4:
            atr_z = float((atr_pct - atr_series_pct.mean()) / atr_series_pct.std())

        # Price return z-score for flash-crash / abnormal candle detection
        returns = close.pct_change().dropna()
        ret_std = float(returns.std()) if len(returns) >= 20 else 0.0
        latest_ret = float(returns.iloc[-1]) if not returns.empty else 0.0
        ret_z = abs(latest_ret) / ret_std if ret_std > 1e-4 else 0.0

        # Normalized EMA slope
        slope = (curr_ema20 - float(ema_20.iloc[-6])) / curr_close if curr_close > 0 else 0.0
        ema_spread = (curr_ema20 - curr_ema50) / curr_ema50 if curr_ema50 > 0 else 0.0

        # Bollinger bandwidth for expansion vs contraction
        upper, mid, lower = Indicators.bollinger_bands(close, 20, 2.0)
        bb_width = ((upper - lower) / mid.replace(0.0, np.nan)).dropna()
        bb_pct_rank = float(bb_width.rank(pct=True).iloc[-1]) if len(bb_width) >= 10 else 0.5
        bb_expanding = len(bb_width) >= 5 and float(bb_width.iloc[-1]) > float(bb_width.iloc[-5]) * 1.20
        bb_contracting = len(bb_width) >= 5 and float(bb_width.iloc[-1]) < float(bb_width.iloc[-5]) * 0.85

        metrics = {
            "adx": round(curr_adx, 2),
            "hist_vol": round(curr_hv, 6),
            "atr_pct": round(atr_pct, 6),
            "atr_zscore": round(atr_z, 3),
            "latest_return_z": round(ret_z, 3),
            "ema_spread": round(ema_spread, 5),
            "bb_width_rank": round(bb_pct_rank, 3),
        }

        secondary: list[MarketRegime] = []
        if bb_expanding:
            secondary.append(MarketRegime.VOLATILITY_EXPANSION)
        elif bb_contracting:
            secondary.append(MarketRegime.VOLATILITY_CONTRACTION)

        if curr_hv > max(hv_median * 1.8, 0.025) or atr_z > 2.0:
            secondary.append(MarketRegime.HIGH_VOLATILITY)
        elif curr_hv < min(hv_median * 0.6, 0.005) and bb_pct_rank < 0.25:
            secondary.append(MarketRegime.LOW_VOLATILITY)

        # 1. Abnormal / unstable check takes absolute priority (survival-first)
        if (ret_z > 4.0 and abs(latest_ret) > 0.015) or (atr_z > 3.5 and atr_pct > 0.015) or abs(latest_ret) > 0.08:
            return RegimeAssessment(
                primary_regime=MarketRegime.ABNORMAL_UNSTABLE,
                secondary_regimes=secondary,
                confidence=min(0.70 + min((ret_z - 3.5) * 0.05, 0.25), 0.95),
                volatility_pct=curr_hv,
                trend_strength_adx=curr_adx,
                trend_slope=slope,
                reason=f"Abnormal market shock: return_z={ret_z:.2f}, atr_z={atr_z:.2f}, latest_ret={latest_ret:.2%}",
                metrics=metrics,
            )

        # 2. High volatility regime
        if curr_hv > max(hv_median * 2.0, 0.03) or atr_z > 2.3:
            return RegimeAssessment(
                primary_regime=MarketRegime.HIGH_VOLATILITY,
                secondary_regimes=secondary,
                confidence=round(min(0.60 + atr_z * 0.08, 0.90), 4),
                volatility_pct=curr_hv,
                trend_strength_adx=curr_adx,
                trend_slope=slope,
                reason=f"Elevated volatility regime: HV={curr_hv:.4f}, ATR_Z={atr_z:.2f}",
                metrics=metrics,
            )

        # 3. Trending regimes (bullish / bearish)
        if curr_adx >= 18.0 and (abs(ema_spread) >= 0.001 or abs(slope) >= 0.002):
            if curr_ema20 > curr_ema50 and (slope >= -0.0015 or abs(ema_spread) >= 0.0025):
                conf = min(0.55 + (curr_adx - 18.0) / 50.0 + min(abs(ema_spread) * 15.0, 0.20), 0.92)
                return RegimeAssessment(
                    primary_regime=MarketRegime.BULLISH_TRENDING,
                    secondary_regimes=secondary,
                    confidence=round(conf, 4),
                    volatility_pct=curr_hv,
                    trend_strength_adx=curr_adx,
                    trend_slope=slope,
                    reason=f"Bullish trend: EMA20>EMA50 (+{ema_spread:.2%}), ADX={curr_adx:.1f}",
                    metrics=metrics,
                )
            if curr_ema20 < curr_ema50 and (slope <= 0.0015 or abs(ema_spread) >= 0.0025):
                conf = min(0.55 + (curr_adx - 18.0) / 50.0 + min(abs(ema_spread) * 15.0, 0.20), 0.92)
                return RegimeAssessment(
                    primary_regime=MarketRegime.BEARISH_TRENDING,
                    secondary_regimes=secondary,
                    confidence=round(conf, 4),
                    volatility_pct=curr_hv,
                    trend_strength_adx=curr_adx,
                    trend_slope=slope,
                    reason=f"Bearish trend: EMA20<EMA50 ({ema_spread:.2%}), ADX={curr_adx:.1f}",
                    metrics=metrics,
                )

        # 4. Volatility expansion / contraction / low-vol / sideways
        if (bb_expanding and curr_adx >= 13.0) or (atr_z >= 0.75 and curr_adx >= 15.0):
            return RegimeAssessment(
                primary_regime=MarketRegime.VOLATILITY_EXPANSION,
                secondary_regimes=secondary,
                confidence=0.68,
                volatility_pct=curr_hv,
                trend_strength_adx=curr_adx,
                trend_slope=slope,
                reason=f"Volatility expansion underway (BB expanding, ATR_Z={atr_z:.2f}, ADX={curr_adx:.1f}).",
                metrics=metrics,
            )

        if bb_contracting and bb_pct_rank < 0.35:
            return RegimeAssessment(
                primary_regime=MarketRegime.VOLATILITY_CONTRACTION,
                secondary_regimes=secondary,
                confidence=0.70,
                volatility_pct=curr_hv,
                trend_strength_adx=curr_adx,
                trend_slope=slope,
                reason=f"Volatility contraction / squeeze (BB rank={bb_pct_rank:.2f}).",
                metrics=metrics,
            )

        if MarketRegime.LOW_VOLATILITY in secondary and curr_adx < 24.0:
            return RegimeAssessment(
                primary_regime=MarketRegime.LOW_VOLATILITY,
                secondary_regimes=[MarketRegime.SIDEWAYS, *secondary],
                confidence=0.68,
                volatility_pct=curr_hv,
                trend_strength_adx=curr_adx,
                trend_slope=slope,
                reason=f"Low-volatility range-bound market (ADX={curr_adx:.1f}, HV={curr_hv:.4f}).",
                metrics=metrics,
            )

        # Classify remaining valid transitional structures into directional or range regimes instead of UNKNOWN
        if abs(ema_spread) >= 0.004 and slope >= 0.001 and curr_ema20 > curr_ema50:
            return RegimeAssessment(
                primary_regime=MarketRegime.BULLISH_TRENDING,
                secondary_regimes=[MarketRegime.SIDEWAYS, *secondary],
                confidence=0.58,
                volatility_pct=curr_hv,
                trend_strength_adx=curr_adx,
                trend_slope=slope,
                reason=f"Developing bullish slope (EMA spread={ema_spread:.2%}, ADX={curr_adx:.1f}).",
                metrics=metrics,
            )
        if abs(ema_spread) >= 0.004 and slope <= -0.001 and curr_ema20 < curr_ema50:
            return RegimeAssessment(
                primary_regime=MarketRegime.BEARISH_TRENDING,
                secondary_regimes=[MarketRegime.SIDEWAYS, *secondary],
                confidence=0.58,
                volatility_pct=curr_hv,
                trend_strength_adx=curr_adx,
                trend_slope=slope,
                reason=f"Developing bearish slope (EMA spread={ema_spread:.2%}, ADX={curr_adx:.1f}).",
                metrics=metrics,
            )

        return RegimeAssessment(
            primary_regime=MarketRegime.SIDEWAYS,
            secondary_regimes=secondary,
            confidence=round(max(0.52, min(0.60 + (22.0 - curr_adx) / 40.0, 0.85)), 4),
            volatility_pct=curr_hv,
            trend_strength_adx=curr_adx,
            trend_slope=slope,
            reason=f"Range / transitional consolidation (ADX={curr_adx:.1f}, EMA spread={ema_spread:.3%}).",
            metrics=metrics,
        )
