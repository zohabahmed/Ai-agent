"""Failure-injection tests verifying safe degradation under outages, stale data, duplicates, and crashes."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.backtesting.historical_data import HistoricalDataLoader
from app.config import TradingMode
from app.database.repositories.trading_repo import TradingRepository
from app.exchanges.base_exchange import OrderRequest, Ticker
from app.exchanges.exchange_health import ExchangeHealthMonitor
from app.execution.execution_engine import ExecutionEngine
from app.execution.order_manager import OrderManager
from app.intelligence.regime_detector import MarketRegime
from app.market.data_validator import MarketDataValidator
from app.risk.drawdown_monitor import DrawdownMonitor
from app.risk.survival_controller import SurvivalController, SurvivalState
from tests.conftest import MockExchange


@pytest.mark.asyncio
async def test_exchange_api_outage_trips_circuit_breaker_and_halts_survival():
    bad_exchange = MockExchange(fail_ping=True)
    monitor = ExchangeHealthMonitor(failure_threshold=2)

    s1 = await monitor.check(bad_exchange)
    assert s1.is_healthy is False
    s2 = await monitor.check(bad_exchange)
    assert s2.circuit_open is True

    sc = SurvivalController()
    dd = DrawdownMonitor(Decimal("0.05"), Decimal("0.01")).evaluate(
        peak_equity=Decimal("100"), current_equity=Decimal("100"), day_start_equity=Decimal("100")
    )
    verdict = sc.evaluate(
        drawdown=dd,
        db_healthy=True,
        exchange_healthy=not s2.circuit_open,
        market_data_valid=True,
        balance_verified=True,
        market_regime=MarketRegime.BULLISH_TRENDING,
    )
    assert verdict.state == SurvivalState.HALTED
    assert verdict.new_trades_allowed is False


def test_db_outage_and_unverified_balance_halt_trading():
    sc = SurvivalController()
    dd = DrawdownMonitor(Decimal("0.05"), Decimal("0.01")).evaluate(
        peak_equity=Decimal("100"), current_equity=Decimal("100"), day_start_equity=Decimal("100")
    )

    v_db = sc.evaluate(
        drawdown=dd,
        db_healthy=False,
        exchange_healthy=True,
        market_data_valid=True,
        balance_verified=True,
        market_regime=MarketRegime.BULLISH_TRENDING,
    )
    assert v_db.state == SurvivalState.HALTED
    assert v_db.new_trades_allowed is False

    v_bal = sc.evaluate(
        drawdown=dd,
        db_healthy=True,
        exchange_healthy=True,
        market_data_valid=True,
        balance_verified=False,
        market_regime=MarketRegime.BULLISH_TRENDING,
    )
    assert v_bal.state == SurvivalState.HALTED
    assert v_bal.new_trades_allowed is False


def test_stale_market_data_and_duplicate_candles_rejected():
    validator = MarketDataValidator(stale_max_seconds=60, max_spread_bps=Decimal("25"))
    old_candles = HistoricalDataLoader.generate_synthetic_regime_series(bars=30, seed=1)
    # Duplicate the last candle
    old_candles.append(dict(old_candles[-1]))
    rep = validator.validate_candles(old_candles, timeframe="15m", allow_historical=False)
    assert rep.duplicates_removed == 1
    assert rep.is_valid is False  # Candles from Jan 2026 are stale relative to now

    stale_ticker = Ticker(
        symbol="BTC/USDT",
        last=Decimal("65000"),
        bid=Decimal("64990"),
        ask=Decimal("65010"),
        volume_24h=Decimal("1000"),
        timestamp=datetime.now(timezone.utc) - timedelta(seconds=300),
    )
    ok, errs = validator.validate_ticker(stale_ticker)
    assert ok is False
    assert any("Stale ticker" in e for e in errs)


@pytest.mark.asyncio
async def test_duplicate_order_attempt_and_ambiguous_execution_error(db_manager):
    exchange = MockExchange(fail_create_order=False)
    survival = SurvivalController()
    engine = ExecutionEngine(exchange=exchange, survival=survival)

    async with db_manager.session() as session:
        repo = TradingRepository(session)
        account = await repo.get_or_create_default_account(initial_capital=Decimal("200.00"))
        rules = await exchange.get_market_rules("BTC/USDT")
        ticker = await exchange.fetch_ticker("BTC/USDT")
        cid = OrderManager.build_client_order_id("dup")
        req = OrderRequest(
            symbol="BTC/USDT",
            side="BUY",
            order_type="MARKET",
            quantity=Decimal("0.001000"),
            client_order_id=cid,
        )

        first = await engine.execute(
            session=session,
            account_id=account.id,
            risk_decision_id=None,
            request=req,
            rules=rules,
            ticker=ticker,
            order_book=None,
            trading_mode=TradingMode.PAPER,
            live_authorized=False,
            strategy_name="trend_following",
            market_regime="BULLISH_TRENDING",
        )
        assert first.executed is True

        # Second attempt with identical client_order_id MUST be skipped idempotently
        second = await engine.execute(
            session=session,
            account_id=account.id,
            risk_decision_id=None,
            request=req,
            rules=rules,
            ticker=ticker,
            order_book=None,
            trading_mode=TradingMode.PAPER,
            live_authorized=False,
            strategy_name="trend_following",
            market_regime="BULLISH_TRENDING",
        )
        assert second.executed is False
        assert second.duplicate_skipped is True

    # Test ambiguous live/testnet execution failure -> marks OPEN for reconciliation rather than retrying blindly
    failing_exchange = MockExchange(fail_create_order=True)
    failing_engine = ExecutionEngine(exchange=failing_exchange, survival=survival)
    async with db_manager.session() as session:
        repo = TradingRepository(session)
        account = await repo.get_or_create_default_account()
        rules = await failing_exchange.get_market_rules("BTC/USDT")
        ticker = await failing_exchange.fetch_ticker("BTC/USDT")
        req2 = OrderRequest(
            symbol="BTC/USDT",
            side="BUY",
            order_type="MARKET",
            quantity=Decimal("0.001000"),
            client_order_id=OrderManager.build_client_order_id("ambig"),
        )
        out = await failing_engine.execute(
            session=session,
            account_id=account.id,
            risk_decision_id=None,
            request=req2,
            rules=rules,
            ticker=ticker,
            order_book=None,
            trading_mode=TradingMode.TESTNET,
            live_authorized=True,
            strategy_name="trend_following",
            market_regime="BULLISH_TRENDING",
        )
        assert out.executed is False
        assert out.ambiguous_error is True
        assert out.order is not None
        assert out.order.status == "OPEN"
