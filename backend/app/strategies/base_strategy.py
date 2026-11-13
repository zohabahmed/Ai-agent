"""Base strategy contract and shared quantitative indicator calculations."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class StrategySignal:
    strategy: str
    symbol: str
    action: str  # BUY, SELL, HOLD
    confidence: float
    entry_price: Decimal
    stop_loss: Decimal
    take_profit: Decimal
    reason: str
    indicators: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy,
            "symbol": self.symbol,
            "action": self.action,
            "confidence": round(self.confidence, 4),
            "entry_price": float(self.entry_price),
            "stop_loss": float(self.stop_loss),
            "take_profit": float(self.take_profit),
            "reason": self.reason,
        }


class Indicators:
    """Pure NumPy/Pandas vectorized technical indicators (zero look-ahead)."""

    @staticmethod
    def ema(series: pd.Series, span: int) -> pd.Series:
        return series.ewm(span=span, adjust=False).mean()

    @staticmethod
    def sma(series: pd.Series, window: int) -> pd.Series:
        return series.rolling(window=window, min_periods=window).mean()

    @staticmethod
    def rsi(series: pd.Series, period: int = 14) -> pd.Series:
        delta = series.diff()
        gain = delta.clip(lower=0.0)
        loss = -delta.clip(upper=0.0)
        avg_gain = gain.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
        avg_loss = loss.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
        rs = avg_gain / avg_loss.replace(0.0, np.nan)
        rsi = 100.0 - (100.0 / (1.0 + rs))
        return rsi.fillna(50.0)

    @staticmethod
    def macd(
        series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9
    ) -> tuple[pd.Series, pd.Series, pd.Series]:
        ema_fast = Indicators.ema(series, fast)
        ema_slow = Indicators.ema(series, slow)
        macd_line = ema_fast - ema_slow
        signal_line = Indicators.ema(macd_line, signal)
        hist = macd_line - signal_line
        return macd_line, signal_line, hist

    @staticmethod
    def bollinger_bands(
        series: pd.Series, window: int = 20, num_std: float = 2.0
    ) -> tuple[pd.Series, pd.Series, pd.Series]:
        mid = Indicators.sma(series, window)
        std = series.rolling(window=window, min_periods=window).std(ddof=0)
        upper = mid + num_std * std
        lower = mid - num_std * std
        return upper, mid, lower

    @staticmethod
    def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
        high = df["high"]
        low = df["low"]
        close = df["close"]
        prev_close = close.shift(1)
        tr = pd.concat(
            [
                (high - low),
                (high - prev_close).abs(),
                (low - prev_close).abs(),
            ],
            axis=1,
        ).max(axis=1)
        return tr.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()

    @staticmethod
    def historical_volatility(series: pd.Series, window: int = 20) -> pd.Series:
        log_ret = np.log(series / series.shift(1))
        return log_ret.rolling(window=window, min_periods=window).std(ddof=1)

    @staticmethod
    def trend_strength_adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
        high = df["high"]
        low = df["low"]
        up_move = high.diff()
        down_move = -low.diff()
        plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
        minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
        atr = Indicators.atr(df, period).replace(0.0, np.nan)
        plus_di = 100.0 * pd.Series(plus_dm, index=df.index).ewm(alpha=1.0 / period, adjust=False).mean() / atr
        minus_di = 100.0 * pd.Series(minus_dm, index=df.index).ewm(alpha=1.0 / period, adjust=False).mean() / atr
        dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0.0, np.nan)
        return dx.ewm(alpha=1.0 / period, adjust=False).mean().fillna(0.0)


class BaseStrategy(ABC):
    """Abstract base strategy returning standardized StrategySignal."""

    name: str
    display_name: str
    category: str
    preferred_regimes: list[str]
    min_candles: int = 60

    def __init__(self, params: dict[str, Any] | None = None) -> None:
        self.params: dict[str, Any] = self.default_params()
        if params:
            self.params.update(params)

    @classmethod
    def default_params(cls) -> dict[str, Any]:
        return {}

    @abstractmethod
    def generate_signal(self, symbol: str, df: pd.DataFrame) -> StrategySignal:
        """Analyze OHLCV DataFrame up to the latest closed bar and produce a signal."""

    @staticmethod
    def hold_signal(strategy: str, symbol: str, price: float, reason: str, indicators: dict[str, float] | None = None) -> StrategySignal:
        p = Decimal(str(round(price, 8))) if price > 0 else Decimal("0")
        return StrategySignal(
            strategy=strategy,
            symbol=symbol,
            action="HOLD",
            confidence=0.0,
            entry_price=p,
            stop_loss=p,
            take_profit=p,
            reason=reason,
            indicators=indicators or {},
        )
