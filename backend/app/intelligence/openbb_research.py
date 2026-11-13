"""OpenBB Open Data Platform (ODP) Quantitative Research & Confluence Engine.

Uses openbb-core, openbb-technical (pandas-ta-openbb), and statsmodels to compute
multi-factor technical confluence, statistical stationarity/autocorrelation checks,
and conservative quality gating for strategy signals.

NOTE: Quantitative filtering improves signal selectivity and reduces whipsaw entries,
but cannot guarantee profitability or a fixed win rate in stochastic financial markets.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

try:
    import pandas_ta as pta
    from openbb_core.app.model.obbject import OBBject
    from statsmodels.tsa.stattools import adfuller

    OPENBB_AVAILABLE = True
except Exception:  # pragma: no cover - optional degraded fallback
    pta = None
    OBBject = None
    adfuller = None
    OPENBB_AVAILABLE = False


@dataclass(frozen=True)
class OpenBBQuantReport:
    openbb_enabled: bool
    provider: str
    confluence_score: float  # [0.0, 1.0] fraction of aligned bullish technical/volume factors
    trend_bias: str  # BULLISH, BEARISH, NEUTRAL
    adx_14: float
    di_spread: float  # +DI - -DI
    supertrend_bullish: bool
    obv_accumulation: bool
    rsi_14: float
    lag1_autocorr: float
    adf_pvalue: float
    quality_gate_passed: bool
    gate_reasons: list[str] = field(default_factory=list)

    def allows_strategy_entry(self, strategy_name: str, action: str = "BUY") -> tuple[bool, str]:
        """Conservative quality filter; complements EV, Kelly, and SurvivalController."""
        side = action.upper()
        if side == "SELL":
            bear_score = 1.0 - self.confluence_score
            if bear_score < 0.40:
                return (
                    False,
                    f"OpenBB bearish confluence too weak ({bear_score:.2f} < 0.40) for SHORT entry.",
                )
            if strategy_name in {"trend_following", "momentum"} and self.supertrend_bullish and self.di_spread >= 0:
                return (
                    False,
                    f"OpenBB SuperTrend/DI opposes {strategy_name} SHORT (DI_spread={self.di_spread:.2f})",
                )
            return True, "OpenBB multi-factor bearish confluence confirmed"

        if self.confluence_score < 0.40:
            return (
                False,
                f"OpenBB confluence too weak ({self.confluence_score:.2f} < 0.40): {', '.join(self.gate_reasons)}",
            )

        if strategy_name in {"trend_following", "momentum"}:
            if not self.supertrend_bullish and self.di_spread <= 0:
                return (
                    False,
                    f"OpenBB SuperTrend/DI opposes {strategy_name} long (DI_spread={self.di_spread:.2f})",
                )
        elif strategy_name == "mean_reversion":
            # Avoid catching falling knives during strong bearish impulse
            if self.di_spread < -18.0 and not self.obv_accumulation:
                return (
                    False,
                    f"OpenBB bearish momentum extreme (DI_spread={self.di_spread:.2f}); preserving capital",
                )
        return True, "OpenBB multi-factor confluence confirmed"

    def to_dict(self) -> dict[str, Any]:
        return {
            "openbb_enabled": self.openbb_enabled,
            "provider": self.provider,
            "confluence_score": round(self.confluence_score, 4),
            "trend_bias": self.trend_bias,
            "adx_14": round(self.adx_14, 2),
            "di_spread": round(self.di_spread, 2),
            "supertrend_bullish": self.supertrend_bullish,
            "obv_accumulation": self.obv_accumulation,
            "rsi_14": round(self.rsi_14, 2),
            "lag1_autocorr": round(self.lag1_autocorr, 4),
            "adf_pvalue": round(self.adf_pvalue, 4),
            "quality_gate_passed": self.quality_gate_passed,
            "gate_reasons": self.gate_reasons,
        }

    def to_obbject(self) -> Any:
        payload = self.to_dict()
        if OBBject is not None:
            return OBBject(results=payload, provider=self.provider)
        return payload


class OpenBBQuantResearch:
    """Computes OpenBB/ODP multi-indicator confluence & statsmodels regime diagnostics."""

    PROVIDER_LABEL = "openbq-org/OpenBB (openbb-technical + statsmodels)"

    def analyze(self, df: pd.DataFrame) -> OpenBBQuantReport:
        if df is None or len(df) < 35 or not OPENBB_AVAILABLE or pta is None:
            return OpenBBQuantReport(
                openbb_enabled=OPENBB_AVAILABLE,
                provider=self.PROVIDER_LABEL,
                confluence_score=0.5,
                trend_bias="NEUTRAL",
                adx_14=0.0,
                di_spread=0.0,
                supertrend_bullish=False,
                obv_accumulation=False,
                rsi_14=50.0,
                lag1_autocorr=0.0,
                adf_pvalue=1.0,
                quality_gate_passed=False,
                gate_reasons=["Insufficient candles (<35) or OpenBB technical dependencies unavailable."],
            )

        high = df["high"].astype(float)
        low = df["low"].astype(float)
        close = df["close"].astype(float)
        volume = df["volume"].astype(float)

        # 1. ADX & Directional Movement via pandas-ta-openbb
        adx_df = pta.adx(high, low, close, length=14)
        adx_val = float(self._col_last(adx_df, "ADX_", default=15.0))
        dmp_val = float(self._col_last(adx_df, "DMP_", default=0.0))
        dmn_val = float(self._col_last(adx_df, "DMN_", default=0.0))
        di_spread = dmp_val - dmn_val

        # 2. SuperTrend direction (+1 bullish, -1 bearish)
        st_df = pta.supertrend(high, low, close, length=7, multiplier=3.0)
        st_dir = float(self._col_last(st_df, "SUPERTd_", default=0.0))
        supertrend_bullish = st_dir > 0

        # 3. RSI (14) & MACD histogram (12, 26, 9)
        rsi_series = pta.rsi(close, length=14)
        rsi_val = float(self._series_last(rsi_series, default=50.0))
        macd_df = pta.macd(close, fast=12, slow=26, signal=9)
        macd_hist = float(self._col_last(macd_df, "MACDh_", default=0.0))

        # 4. On-Balance Volume accumulation vs 20-bar EMA
        obv_series = pta.obv(close, volume)
        obv_acc = False
        if obv_series is not None and len(obv_series.dropna()) >= 20:
            obv_ema = obv_series.ewm(span=20, adjust=False).mean()
            obv_acc = bool(float(obv_series.iloc[-1]) > float(obv_ema.iloc[-1]))

        # 5. Statistical persistence (lag-1 autocorrelation & ADF stationarity on log returns)
        log_ret = np.log(close / close.shift(1)).dropna()
        lag1_ac = float(log_ret.autocorr(lag=1)) if len(log_ret) > 10 else 0.0
        if np.isnan(lag1_ac):
            lag1_ac = 0.0

        adf_p = 1.0
        if adfuller is not None and len(log_ret) >= 20 and log_ret.std() > 0:
            try:
                adf_p = float(adfuller(log_ret.values, maxlag=4, autolag=None)[1])
            except Exception:
                adf_p = 1.0

        # Multi-factor bullish alignment score (5 independent checks)
        checks = {
            "supertrend_bullish": supertrend_bullish,
            "directional_di_positive": di_spread > 0 and adx_val >= 15.0,
            "macd_momentum_positive": macd_hist > 0,
            "obv_accumulation": obv_acc,
            "rsi_healthy_zone": 30.0 <= rsi_val <= 68.0,
        }
        confluence = sum(1 for v in checks.values() if v) / float(len(checks))

        reasons: list[str] = []
        if not supertrend_bullish:
            reasons.append("SuperTrend bearish")
        if not checks["directional_di_positive"]:
            reasons.append(f"Weak/bearish DI trend (ADX={adx_val:.1f}, DI_spread={di_spread:.1f})")
        if not checks["macd_momentum_positive"]:
            reasons.append("MACD histogram <= 0")
        if not obv_acc:
            reasons.append("OBV below 20-EMA (no net accumulation)")
        if not checks["rsi_healthy_zone"]:
            reasons.append(f"RSI extreme/overheated ({rsi_val:.1f})")

        if confluence >= 0.60 and supertrend_bullish:
            trend_bias = "BULLISH"
        elif confluence <= 0.25 and not supertrend_bullish:
            trend_bias = "BEARISH"
        else:
            trend_bias = "NEUTRAL"

        quality_ok = confluence >= 0.60 and not (di_spread < -5.0 and not obv_acc)

        return OpenBBQuantReport(
            openbb_enabled=True,
            provider=self.PROVIDER_LABEL,
            confluence_score=confluence,
            trend_bias=trend_bias,
            adx_14=adx_val,
            di_spread=di_spread,
            supertrend_bullish=supertrend_bullish,
            obv_accumulation=obv_acc,
            rsi_14=rsi_val,
            lag1_autocorr=lag1_ac,
            adf_pvalue=adf_p,
            quality_gate_passed=quality_ok,
            gate_reasons=reasons,
        )

    @staticmethod
    def _col_last(frame: pd.DataFrame | None, prefix: str, *, default: float) -> float:
        if frame is None or frame.empty:
            return default
        col = next((c for c in frame.columns if c.startswith(prefix)), None)
        if col is None:
            return default
        val = frame[col].iloc[-1]
        return float(val) if pd.notna(val) else default

    @staticmethod
    def _series_last(series: pd.Series | None, *, default: float) -> float:
        if series is None or series.empty:
            return default
        val = series.iloc[-1]
        return float(val) if pd.notna(val) else default
