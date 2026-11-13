"""Reproducible walk-forward experiment runner comparing all registered strategies."""

from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from app.adaptive.strategy_evaluator import StrategyEvaluator  # noqa: E402
from app.backtesting.historical_data import HistoricalDataLoader  # noqa: E402
from app.market.candle_manager import CandleManager  # noqa: E402
from app.strategies.strategy_registry import StrategyRegistry  # noqa: E402


def run_experiment() -> None:
    candles = HistoricalDataLoader.generate_synthetic_regime_series(bars=400, seed=42)
    df = CandleManager.to_dataframe(candles)
    registry = StrategyRegistry()
    evaluator = StrategyEvaluator()
    results = evaluator.evaluate_candidates(
        candidates=registry.list_all(),
        symbol="BTC/USDT",
        df=df,
    )
    for res in results:
        print(
            f"{res.strategy_name:20s} | passed={str(res.passed):5s} | "
            f"score={res.score:7.3f} | {res.walk_forward.reason}"
        )


if __name__ == "__main__":
    run_experiment()
