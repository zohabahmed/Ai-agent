"""Unit & integration tests for Binance Futures isolated-margin balance sync, position reconciliation, and MARKET fill price resolution."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
import uuid

import pytest
from sqlalchemy import select

from app.config import TradingMode
from app.database.models import Balance, Order, Position, RiskDecision
from app.database.repositories.trading_repo import TradingRepository
from app.exchanges.base_exchange import (
    BaseExchange,
    ExchangeBalance,
    ExchangeError,
    ExchangePosition,
    MarketRules,
    OrderBook,
    OrderRequest,
    OrderResult,
    Ticker,
)
from app.exchanges.ccxt_adapter import CCXTExchangeAdapter
from app.exchanges.credentials_service import ExchangeConnectionService
from app.execution.execution_engine import ExecutionEngine
from app.execution.reconciliation import ReconciliationService
from app.portfolio.equity_calculator import EquityCalculator
from app.risk.survival_controller import SurvivalController


class _FakeCCXTClient:
    """Minimal CCXT client double reproducing Binance USD-M Futures payloads."""

    def __init__(self) -> None:
        self.markets = {
            "BNB/USDT:USDT": {
                "symbol": "BNB/USDT:USDT",
                "base": "BNB",
                "quote": "USDT",
                "swap": True,
                "active": True,
                "contractSize": 1,
                "precision": {"amount": 0.01, "price": 0.01},
                "limits": {"amount": {"min": 0.01}, "cost": {"min": 5}},
                "taker": 0.0005,
                "maker": 0.0002,
            },
            "ARB/USDT:USDT": {
                "symbol": "ARB/USDT:USDT",
                "base": "ARB",
                "quote": "USDT",
                "swap": True,
                "active": True,
                "contractSize": 1,
                "precision": {"amount": 0.1, "price": 0.0001},
                "limits": {"amount": {"min": 0.1}, "cost": {"min": 5}},
                "taker": 0.0005,
                "maker": 0.0002,
            },
        }
        self.last_create_params: dict[str, Any] = {}
        self.create_order_response: dict[str, Any] = {}
        self.fetch_order_response: dict[str, Any] = {}
        self.ticker_response: dict[str, Any] = {"last": 631.72, "bid": 631.7, "ask": 631.75, "quoteVolume": 1000000}

    def market(self, symbol: str) -> dict[str, Any]:
        return self.markets[symbol]

    async def load_markets(self) -> dict[str, Any]:
        return self.markets

    async def close(self) -> None:
        return None

    async def fetch_balance(self) -> dict[str, Any]:
        return {
            "free": {"USDT": 0.01317302},
            "used": {"USDT": 3.35610669},
            "total": {"USDT": 4.94209952},
            "info": {
                "assets": [
                    {
                        "asset": "USDT",
                        "walletBalance": "4.94317826",
                        "unrealizedProfit": "-0.00107874",
                        "marginBalance": "4.94209952",
                        "maintMargin": "0.07423056",
                        "initialMargin": "3.35610669",
                        "positionInitialMargin": "3.35610669",
                        "availableBalance": "0.01317302",
                    }
                ]
            },
        }

    async def fetch_positions(self) -> list[dict[str, Any]]:
        return [
            {
                "symbol": "BNB/USDT:USDT",
                "side": "short",
                "contracts": 0.01,
                "contractSize": 1,
                "entryPrice": 631.72,
                "markPrice": 631.6,
                "notional": 6.316,
                "initialMargin": 1.579,
                "collateral": 3.15704445,
                "unrealizedPnl": 0.0012,
                "marginMode": "isolated",
                "liquidationPrice": 942.59148756,
                "leverage": None,
                "stopLossPrice": None,
                "takeProfitPrice": None,
                "info": {
                    "isolatedWallet": "3.15584445",
                    "positionInitialMargin": "1.579",
                },
            },
            {
                "symbol": "ARB/USDT:USDT",
                "side": "short",
                "contracts": 48.9,
                "contractSize": 1,
                "entryPrice": 0.14532,
                "markPrice": 0.14547,
                "notional": 7.113483,
                "initialMargin": 1.77837075,
                "collateral": 1.76682579,
                "unrealizedPnl": -0.007335,
                "marginMode": "isolated",
                "liquidationPrice": 0.1805183,
                "leverage": None,
                "stopLossPrice": None,
                "takeProfitPrice": None,
                "info": {
                    "isolatedWallet": "1.77416079",
                    "positionInitialMargin": "1.77837075",
                },
            },
            {
                "symbol": "BTC/USDT:USDT",
                "side": None,
                "contracts": 0,
                "contractSize": 1,
                "entryPrice": 0,
                "markPrice": 0,
            },
        ]

    async def fetch_open_orders(self, symbol: str | None = None) -> list[dict[str, Any]]:
        return []

    async def create_order(
        self,
        *,
        symbol: str,
        type: str,
        side: str,
        amount: float,
        price: float | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.last_create_params = dict(params or {})
        return self.create_order_response

    async def fetch_order(self, order_id: str, symbol: str) -> dict[str, Any]:
        return self.fetch_order_response

    async def fetch_ticker(self, symbol: str) -> dict[str, Any]:
        return self.ticker_response


class _FuturesMockExchange(BaseExchange):
    """Programmable async BaseExchange double for sync/reconciliation/execution DB tests."""

    def __init__(self) -> None:
        self.exchange_id = "binance"
        self.balances: dict[str, ExchangeBalance] = {}
        self.positions: list[ExchangePosition] = []
        self.open_orders_by_symbol: dict[str, list[OrderResult]] = {}
        self.fail_error: str | None = None

    async def connect(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def ping(self) -> float:
        return 10.0

    async def get_market_rules(self, symbol: str) -> MarketRules:
        base, quote = symbol.split("/")
        return MarketRules(
            symbol=symbol,
            base_asset=base,
            quote_asset=quote,
            min_quantity=Decimal("0.01"),
            min_notional=Decimal("5"),
            quantity_precision=2,
            price_precision=4,
            maker_fee=Decimal("0.0002"),
            taker_fee=Decimal("0.0005"),
        )

    async def fetch_ohlcv(self, symbol: str, timeframe: str, since: datetime | None = None, limit: int = 200) -> list[dict[str, Any]]:
        return []

    async def fetch_ticker(self, symbol: str) -> Ticker:
        return Ticker(
            symbol=symbol,
            last=Decimal("631.72"),
            bid=Decimal("631.70"),
            ask=Decimal("631.75"),
            volume_24h=Decimal("100000"),
            timestamp=datetime.now(timezone.utc),
        )

    async def fetch_order_book(self, symbol: str, limit: int = 20) -> OrderBook:
        return OrderBook(symbol=symbol, bids=[], asks=[], timestamp=datetime.now(timezone.utc))

    async def fetch_balances(self) -> dict[str, ExchangeBalance]:
        return dict(self.balances)

    async def fetch_positions(self) -> list[ExchangePosition]:
        return list(self.positions)

    async def fetch_open_orders(self, symbol: str | None = None) -> list[OrderResult]:
        if symbol is None:
            return [o for lst in self.open_orders_by_symbol.values() for o in lst]
        return list(self.open_orders_by_symbol.get(symbol, []))

    async def create_order(self, request: OrderRequest) -> OrderResult:
        if self.fail_error:
            raise ExchangeError(self.fail_error)
        return OrderResult(
            exchange_order_id="ex-1",
            client_order_id=request.client_order_id,
            symbol=request.symbol,
            side=request.side,
            order_type=request.order_type,
            status="FILLED",
            requested_quantity=request.quantity,
            filled_quantity=request.quantity,
            average_price=Decimal("631.72"),
            fee=Decimal("0.003"),
            fee_asset="USDT",
            timestamp=datetime.now(timezone.utc),
        )

    async def cancel_order(self, exchange_order_id: str, symbol: str) -> OrderResult:
        raise NotImplementedError

    async def fetch_order(self, exchange_order_id: str, symbol: str) -> OrderResult:
        raise ExchangeError("unexpected fetch_order call")

    async def fetch_my_trades(self, symbol: str, limit: int = 50) -> list[dict[str, Any]]:
        return []


@pytest.mark.asyncio
async def test_ccxt_binance_futures_balance_preserves_extra_isolated_margin() -> None:
    adapter = CCXTExchangeAdapter(
        "binance",
        api_key="k",
        api_secret="s",
        sandbox=False,
        read_only=True,
        market_type="FUTURES",
        leverage=1,
    )
    adapter._client = _FakeCCXTClient()  # type: ignore[assignment]
    adapter._markets_loaded = True

    balances = await adapter.fetch_balances()
    assert "USDT" in balances
    usdt = balances["USDT"]
    # walletBalance = 4.94317826, availableBalance = 0.01317302 -> used = 4.93000524 (full isolated reserve, NOT 3.3561)
    assert usdt.free == Decimal("0.01317302")
    assert usdt.total == Decimal("4.94317826")
    assert usdt.used == Decimal("4.93000524")
    assert usdt.free + usdt.used == usdt.total


@pytest.mark.asyncio
async def test_ccxt_binance_futures_fetch_positions_normalizes_isolated_shorts() -> None:
    adapter = CCXTExchangeAdapter(
        "binance",
        api_key="k",
        api_secret="s",
        sandbox=False,
        read_only=True,
        market_type="FUTURES",
        leverage=1,
    )
    adapter._client = _FakeCCXTClient()  # type: ignore[assignment]
    adapter._markets_loaded = True

    positions = await adapter.fetch_positions()
    assert len(positions) == 2

    bnb, arb = positions
    assert bnb.symbol == "BNB/USDT"
    assert bnb.side == "SHORT"
    assert bnb.quantity == Decimal("0.01")
    assert bnb.entry_price == Decimal("631.72")
    assert bnb.mark_price == Decimal("631.6")
    assert bnb.margin_used == Decimal("3.15584445")
    assert bnb.leverage == Decimal("4")
    assert bnb.unrealized_pnl == Decimal("0.0012")
    assert bnb.margin_mode == "isolated"

    assert arb.symbol == "ARB/USDT"
    assert arb.side == "SHORT"
    assert arb.quantity == Decimal("48.9")
    assert arb.margin_used == Decimal("1.77416079")
    assert arb.leverage == Decimal("4")
    assert arb.unrealized_pnl == Decimal("-0.007335")


@pytest.mark.asyncio
async def test_ccxt_binance_futures_market_order_resolves_avg_price_and_uses_result_resp() -> None:
    adapter = CCXTExchangeAdapter(
        "binance",
        api_key="k",
        api_secret="s",
        sandbox=False,
        read_only=False,
        market_type="FUTURES",
        leverage=1,
    )
    client = _FakeCCXTClient()
    # Initial MARKET ACK-style payload returned without avgPrice
    client.create_order_response = {
        "id": "98381599767",
        "clientOrderId": "cid-1",
        "symbol": "BNB/USDT:USDT",
        "side": "sell",
        "type": "market",
        "status": "closed",
        "amount": 0.01,
        "filled": 0.01,
        "average": None,
        "price": None,
        "info": {"avgPrice": "0"},
    }
    # Follow-up fetch_order resolves cumQuote / avgPrice
    client.fetch_order_response = {
        "id": "98381599767",
        "clientOrderId": "cid-1",
        "symbol": "BNB/USDT:USDT",
        "side": "sell",
        "type": "market",
        "status": "closed",
        "amount": 0.01,
        "filled": 0.01,
        "average": None,
        "price": None,
        "cost": 6.3172,
        "info": {"cumQuote": "6.3172"},
    }
    adapter._client = client  # type: ignore[assignment]
    adapter._markets_loaded = True

    req = OrderRequest(symbol="BNB/USDT", side="SELL", order_type="MARKET", quantity=Decimal("0.01"), client_order_id="cid-1")
    res = await adapter.create_order(req)

    assert client.last_create_params.get("newOrderRespType") == "RESULT"
    assert res.symbol == "BNB/USDT"
    assert res.filled_quantity == Decimal("0.01")
    assert res.average_price == Decimal("631.72000000")


@pytest.mark.asyncio
async def test_execution_engine_marks_insufficient_margin_as_rejected_not_open(db_manager: Any) -> None:
    ex = _FuturesMockExchange()
    ex.fail_error = 'Order submission failed on binance: InsufficientFunds: binanceusdm {"code":-2019,"msg":"Margin is insufficient."}'
    engine = ExecutionEngine(exchange=ex, survival=SurvivalController())
    rules = await ex.get_market_rules("BNB/USDT")
    ticker = await ex.fetch_ticker("BNB/USDT")

    async with db_manager.session() as session:
        repo = TradingRepository(session)
        acct = await repo.get_or_create_default_account(
            exchange="binance", trading_mode="LIVE_TRADING", initial_capital=Decimal("20.00")
        )
        req = OrderRequest(
            symbol="BNB/USDT",
            side="SELL",
            order_type="MARKET",
            quantity=Decimal("0.01"),
            client_order_id="cid-reject-2019",
        )
        outcome = await engine.execute(
            session=session,
            account_id=acct.id,
            risk_decision_id=None,
            request=req,
            rules=rules,
            ticker=ticker,
            order_book=None,
            trading_mode=TradingMode.LIVE_TRADING,
            live_authorized=True,
            strategy_name="trend_following",
            market_regime="BEARISH_TRENDING",
            market_type="FUTURES",
            leverage=1,
        )
        assert not outcome.executed
        assert not outcome.ambiguous_error
        assert outcome.order is not None
        assert outcome.order.status == "REJECTED"

        # Meanwhile an unknown network timeout must remain ambiguous/OPEN for reconciliation
        ex.fail_error = "Order submission failed on binance: RequestTimeout: timed out"
        req2 = OrderRequest(
            symbol="BNB/USDT",
            side="SELL",
            order_type="MARKET",
            quantity=Decimal("0.01"),
            client_order_id="cid-timeout-1",
        )
        outcome2 = await engine.execute(
            session=session,
            account_id=acct.id,
            risk_decision_id=None,
            request=req2,
            rules=rules,
            ticker=ticker,
            order_book=None,
            trading_mode=TradingMode.LIVE_TRADING,
            live_authorized=True,
            strategy_name="trend_following",
            market_regime="BEARISH_TRENDING",
            market_type="FUTURES",
            leverage=1,
        )
        assert outcome2.ambiguous_error
        assert outcome2.order is not None
        assert outcome2.order.status == "OPEN"


@pytest.mark.asyncio
async def test_verify_and_sync_imports_missing_live_futures_positions_and_clears_phantom_halt(db_manager: Any) -> None:
    ex = _FuturesMockExchange()
    ex.balances = {
        "USDT": ExchangeBalance(
            asset="USDT",
            free=Decimal("0.01317302"),
            used=Decimal("4.93000524"),
            total=Decimal("4.94317826"),
        )
    }
    ex.positions = [
        ExchangePosition(
            symbol="BNB/USDT",
            side="SHORT",
            quantity=Decimal("0.01"),
            entry_price=Decimal("631.72"),
            mark_price=Decimal("631.60"),
            margin_used=Decimal("3.15584445"),
            leverage=Decimal("4"),
            unrealized_pnl=Decimal("0.0012"),
            notional=Decimal("6.316"),
            margin_mode="isolated",
        ),
        ExchangePosition(
            symbol="ARB/USDT",
            side="SHORT",
            quantity=Decimal("48.9"),
            entry_price=Decimal("0.14532"),
            mark_price=Decimal("0.14547"),
            margin_used=Decimal("1.77416079"),
            leverage=Decimal("4"),
            unrealized_pnl=Decimal("-0.007335"),
            notional=Decimal("7.113483"),
            margin_mode="isolated",
        ),
    ]
    ex.open_orders_by_symbol["BNB/USDT"] = [
        OrderResult(
            exchange_order_id="sl-bnb-999",
            client_order_id="cid-sl-bnb",
            symbol="BNB/USDT",
            side="BUY",
            order_type="STOP_MARKET",
            status="OPEN",
            requested_quantity=Decimal("0.01"),
            filled_quantity=Decimal("0"),
            average_price=None,
            fee=Decimal("0"),
            fee_asset="USDT",
            timestamp=datetime.now(timezone.utc),
            raw={"id": "sl-bnb-999", "status": "open", "stopPrice": "641.20"},
        )
    ]

    async with db_manager.session() as session:
        repo = TradingRepository(session)
        acct = await repo.get_or_create_default_account(
            exchange="binance", trading_mode="LIVE_TRADING", initial_capital=Decimal("4.95")
        )
        acct.market_type = "FUTURES"
        acct.leverage = 1
        acct.live_trading_authorized = True
        acct.peak_equity = Decimal("4.9509")
        acct.day_start_equity = Decimal("3.38585473")
        acct.day_start_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        acct.survival_state = "HALTED"
        acct.emergency_stopped = False
        await repo.upsert_balance(acct.id, "USDT", Decimal("0.01317302"), Decimal("3.35502906"))

        rd = RiskDecision(
            account_id=acct.id,
            symbol="BNB/USDT",
            strategy_name="momentum_breakout",
            market_regime="BEARISH_TRENDING",
            survival_state="NORMAL",
            decision="APPROVED",
            approved_notional=Decimal("6.31"),
            approved_quantity=Decimal("0.01"),
        )
        session.add(rd)
        await session.flush()

        # Reproduce missing average_fill_price filled orders and dead rejected OPEN order in DB
        session.add_all(
            [
                Order(
                    account_id=acct.id,
                    risk_decision_id=rd.id,
                    client_order_id="bnb-entry-1",
                    exchange_order_id="98381599767",
                    exchange="binance",
                    symbol="BNB/USDT",
                    side="SELL",
                    order_type="MARKET",
                    trading_mode="LIVE_TRADING",
                    status="FILLED",
                    requested_quantity=Decimal("0.01"),
                    filled_quantity=Decimal("0.01"),
                    average_fill_price=None,
                    strategy_name="momentum_breakout",
                    stop_loss=Decimal("641.20"),
                    take_profit=Decimal("618.00"),
                ),
                Order(
                    account_id=acct.id,
                    client_order_id="arb-entry-1",
                    exchange_order_id="17615514561",
                    exchange="binance",
                    symbol="ARB/USDT",
                    side="SELL",
                    order_type="MARKET",
                    trading_mode="LIVE_TRADING",
                    status="FILLED",
                    requested_quantity=Decimal("48.9"),
                    filled_quantity=Decimal("48.9"),
                    average_fill_price=None,
                    strategy_name="trend_following",
                    stop_loss=Decimal("0.1480"),
                    take_profit=Decimal("0.1410"),
                ),
                Order(
                    account_id=acct.id,
                    client_order_id="doge-failed-1",
                    exchange_order_id=None,
                    exchange="binance",
                    symbol="DOGE/USDT",
                    side="SELL",
                    order_type="MARKET",
                    trading_mode="LIVE_TRADING",
                    status="OPEN",
                    requested_quantity=Decimal("89"),
                    filled_quantity=Decimal("0"),
                    error_message="InsufficientFunds: -2019 Margin is insufficient.",
                ),
            ]
        )
        await session.flush()

        sync = await ExchangeConnectionService.verify_and_sync_balances(
            session,
            account=acct,
            exchange=ex,
            switch_to_read_only_if_paper=False,
        )
        assert sync.verified
        assert sync.positions_synced == 2
        assert Decimal("4.93") <= sync.total_quote <= Decimal("4.95")

        # Balance ledger reflects full walletBalance and isolated locked margin
        bal = (
            await session.execute(select(Balance).where(Balance.account_id == acct.id, Balance.asset == "USDT"))
        ).scalar_one()
        assert bal.total == Decimal("4.94317826")
        assert bal.locked == Decimal("4.93000524")

        # Both positions exist now in DB with restored metadata & open SL order link
        open_pos = await repo.get_open_positions(acct.id)
        assert len(open_pos) == 2
        by_sym = {p.symbol: p for p in open_pos}
        assert by_sym["BNB/USDT"].strategy_name == "momentum_breakout"
        assert by_sym["BNB/USDT"].market_regime == "BEARISH_TRENDING"
        assert by_sym["BNB/USDT"].sl_order_id == "sl-bnb-999"
        assert by_sym["BNB/USDT"].margin_used == Decimal("3.15584445")
        assert by_sym["ARB/USDT"].strategy_name == "trend_following"
        assert by_sym["ARB/USDT"].margin_used == Decimal("1.77416079")

        # Dead unsubmitted order cleaned up & filled order average_fill_price backfilled
        doge_ord = (
            await session.execute(select(Order).where(Order.client_order_id == "doge-failed-1"))
        ).scalar_one()
        assert doge_ord.status == "REJECTED"
        bnb_ord = (
            await session.execute(select(Order).where(Order.client_order_id == "bnb-entry-1"))
        ).scalar_one()
        assert bnb_ord.average_fill_price == Decimal("631.72")

        # Phantom HALTED state cleared & corrupted day_start_equity repaired
        assert acct.survival_state == "NORMAL"
        assert acct.day_start_equity > Decimal("4.90")

        # Simulate BNB closing externally on Binance -> reconcile closes BNB locally
        ex.positions = [ex.positions[1]]
        recon = await ReconciliationService().reconcile(
            session=session,
            account_id=acct.id,
            exchange=ex,
            trading_mode=TradingMode.LIVE_TRADING,
        )
        assert recon.verified
        assert recon.positions_synced == 1
        remaining = await repo.get_open_positions(acct.id)
        assert [p.symbol for p in remaining] == ["ARB/USDT"]


@pytest.mark.asyncio
async def test_equity_calculator_futures_margin_balance_matches_binance_without_double_counting() -> None:
    eq = EquityCalculator.calculate_mixed(
        initial_capital=Decimal("4.95"),
        available_quote=Decimal("0.01317302"),
        locked_quote=Decimal("4.93000524"),
        positions=[
            (
                Decimal("0.01"),
                Decimal("631.72"),
                Decimal("631.60"),
                Decimal("0"),
                "SHORT",
                "FUTURES",
                Decimal("3.15584445"),
            ),
            (
                Decimal("48.9"),
                Decimal("0.14532"),
                Decimal("0.14547"),
                Decimal("0"),
                "SHORT",
                "FUTURES",
                Decimal("1.77416079"),
            ),
        ],
        realized_pnl=Decimal("0"),
        total_fees=Decimal("0"),
    )
    # Equity = availableBalance + (walletBalance - availableBalance) + sum(unrealizedPnl)
    # = 4.94317826 + (0.0012 - 0.007335) = 4.93704326
    assert abs(eq.total_equity - Decimal("4.93704326")) < Decimal("0.0001")
    assert eq.locked_balance == Decimal("4.93000524")
