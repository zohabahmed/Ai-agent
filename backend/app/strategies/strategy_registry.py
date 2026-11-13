"""Strategy registry managing strategy instances, versions, and active statuses."""

from __future__ import annotations

from typing import Any

from app.strategies.base_strategy import BaseStrategy
from app.strategies.mean_reversion import MeanReversionStrategy
from app.strategies.momentum import MomentumStrategy
from app.strategies.trend_following import TrendFollowingStrategy
from app.strategies.volatility_strategy import VolatilityStrategy


class StrategyRegistry:
    """Central registry of validated spot trading strategies."""

    _STRATEGY_CLASSES: dict[str, type[BaseStrategy]] = {
        TrendFollowingStrategy.name: TrendFollowingStrategy,
        MeanReversionStrategy.name: MeanReversionStrategy,
        MomentumStrategy.name: MomentumStrategy,
        VolatilityStrategy.name: VolatilityStrategy,
    }

    def __init__(self) -> None:
        self._instances: dict[str, BaseStrategy] = {
            name: cls() for name, cls in self._STRATEGY_CLASSES.items()
        }
        self._disabled: set[str] = set()

    def get(self, name: str) -> BaseStrategy | None:
        return self._instances.get(name)

    def list_all(self) -> list[BaseStrategy]:
        return list(self._instances.values())

    def list_enabled(self) -> list[BaseStrategy]:
        return [s for name, s in self._instances.items() if name not in self._disabled]

    def disable(self, name: str) -> None:
        if name in self._instances:
            self._disabled.add(name)

    def enable(self, name: str) -> None:
        self._disabled.discard(name)

    def is_enabled(self, name: str) -> bool:
        return name in self._instances and name not in self._disabled

    def update_params(self, name: str, params: dict[str, Any]) -> BaseStrategy:
        cls = self._STRATEGY_CLASSES.get(name)
        if cls is None:
            raise ValueError(f"Unknown strategy: {name}")
        instance = cls(params=params)
        self._instances[name] = instance
        return instance

    @classmethod
    def available_names(cls) -> list[str]:
        return list(cls._STRATEGY_CLASSES.keys())
