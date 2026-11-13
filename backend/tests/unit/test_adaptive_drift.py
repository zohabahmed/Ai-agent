"""Unit tests for DriftDetector and AdaptationManager (strategy deterioration & rollback)."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.adaptive.adaptation_manager import AdaptationManager
from app.adaptive.drift_detector import DriftDetector
from app.adaptive.strategy_versioning import StrategyVersioningService
from app.backtesting.historical_data import HistoricalDataLoader
from app.backtesting.performance_metrics import TradeRecord
from app.database.models import Strategy
from app.market.candle_manager import CandleManager
from app.strategies.strategy_registry import StrategyRegistry


def test_drift_detector_ignores_small_sample_and_detects_real_decay():
    det = DriftDetector(min_trades=12)
    small = det.detect([-0.01, -0.02, -0.015])
    assert small.deteriorated is False

    # Baseline profitable trades followed by sustained negative decay
    baseline = [0.012, 0.008, 0.015, -0.004, 0.010, 0.009, 0.014, -0.003, 0.011, 0.007]
    decayed = [-0.015, -0.012, -0.018, -0.009, -0.020, -0.011, -0.014, -0.016, -0.010, -0.013]
    rep = det.detect(decayed, baseline_returns=baseline)
    assert rep.deteriorated is True
    assert rep.cusum_alarm is True
    assert rep.rolling_ev_negative is True


@pytest.mark.asyncio
async def test_adaptation_manager_and_version_rollback(db_manager):
    registry = StrategyRegistry()
    async with db_manager.session() as session:
        for s in registry.list_all():
            row = Strategy(
                name=s.name,
                display_name=s.display_name,
                category=s.category,
                status="ACTIVE",
                preferred_regimes=s.preferred_regimes,
                parameters=s.params,
            )
            session.add(row)
            await session.flush()
            await StrategyVersioningService.record_version(
                session,
                strategy=row,
                parameters=s.params,
                change_reason="Initial baseline v1",
                validation_summary={"status": "baseline"},
                status="ACTIVE",
                created_by="test",
            )

        # Simulate decaying trade sequence for trend_following
        candles = HistoricalDataLoader.generate_synthetic_regime_series(bars=300, seed=42)
        df = CandleManager.to_dataframe(candles)
        bad_trades = [
            TradeRecord(pnl=0.5, return_pct=0.01, fees=0.01, duration_bars=3) for _ in range(10)
        ] + [
            TradeRecord(pnl=-0.8, return_pct=-0.018, fees=0.01, duration_bars=3) for _ in range(10)
        ]

        mgr = AdaptationManager(registry=registry)
        outcome = await mgr.evaluate_and_adapt(
            session=session,
            active_strategy_name="trend_following",
            recent_trades=bad_trades,
            historical_df=df,
            symbol="BTC/USDT",
            current_regime="SIDEWAYS",
        )
        assert outcome.deterioration_detected is True
        assert registry.is_enabled("trend_following") is False
        assert outcome.diagnosis is not None

        # Test rollback on a strategy with >=2 versions
        res = await session.execute(select(Strategy).where(Strategy.name == "mean_reversion"))
        mr = res.scalar_one()
        await StrategyVersioningService.record_version(
            session,
            strategy=mr,
            parameters={"bb_window": 24},
            change_reason="Test parameter bump v2",
            validation_summary={"oos_ev": 0.01},
            status="ACTIVE",
            created_by="test",
        )
        rolled = await StrategyVersioningService.rollback_to_previous(session, strategy=mr, actor="operator")
        assert rolled.version_number == 1
