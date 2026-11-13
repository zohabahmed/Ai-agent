"""Regime-aware and performance-aware Strategy Selector."""

from __future__ import annotations

from app.intelligence.regime_detector import MarketRegime, RegimeAssessment
from app.strategies.base_strategy import BaseStrategy
from app.strategies.strategy_registry import StrategyRegistry


class StrategySelector:
    """Selects eligible strategies matching the detected market regime and health status."""

    def __init__(self, registry: StrategyRegistry) -> None:
        self.registry = registry

    def select_for_regime(self, regime: RegimeAssessment) -> list[BaseStrategy]:
        if regime.primary_regime in {MarketRegime.ABNORMAL_UNSTABLE, MarketRegime.UNKNOWN}:
            return []

        enabled = self.registry.list_enabled()
        matching: list[BaseStrategy] = []
        for strat in enabled:
            if regime.primary_regime.value in strat.preferred_regimes:
                matching.append(strat)
            elif any(r.value in strat.preferred_regimes for r in regime.secondary_regimes):
                matching.append(strat)

        if not matching:
            matching = [s for s in enabled if s.name in {"volatility_breakout", "mean_reversion"}]
        return matching
