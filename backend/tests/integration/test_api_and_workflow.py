"""Integration tests for FastAPI routes, paper simulator, portfolio reconciliation, and withdrawal workflow."""

from __future__ import annotations

from decimal import Decimal

from httpx import ASGITransport, AsyncClient
import pytest

from app.config import Settings, TradingMode
from app.database.models import Position
from app.database.repositories.trading_repo import TradingRepository
from app.execution.execution_engine import ExecutionEngine
from app.execution.execution_simulator import PaperExecutionSimulator
from app.execution.order_manager import OrderManager
from app.exchanges.base_exchange import OrderRequest
from app.main import bootstrap_initial_state, create_app
from app.portfolio.portfolio_manager import PortfolioManager
from app.risk.survival_controller import SurvivalController
from app.withdrawals.address_manager import AddressManager
from app.withdrawals.approval_service import ApprovalService
from app.withdrawals.withdrawal_manager import WithdrawalManager
from app.workers.trading_worker import AutonomousTradingLoop


@pytest.mark.asyncio
async def test_fastapi_auth_dashboard_and_emergency_stop(db_manager):
    await bootstrap_initial_state()
    app = create_app()
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Health is accessible
        h = await client.get("/api/health")
        assert h.status_code == 200
        assert h.json()["database"] == "connected"
        assert h.json()["trading_mode"] == "PAPER"

        # Unauthenticated dashboard is blocked
        unauth = await client.get("/api/dashboard")
        assert unauth.status_code == 401

        # Authenticate as admin
        tok_res = await client.post(
            "/api/auth/token",
            data={"username": "admin", "password": "admin-dev-password-change-me"},
        )
        assert tok_res.status_code == 200
        token = tok_res.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        dash = await client.get("/api/dashboard", headers=headers)
        assert dash.status_code == 200
        payload = dash.json()
        assert payload["overview"]["initial_capital"] == 1.0
        assert payload["account"]["trading_mode"] == "PAPER"
        assert payload["account"]["is_simulated"] is True

        # Start trading, then trigger emergency stop
        start_res = await client.post("/api/trading/start", headers=headers)
        assert start_res.status_code == 200
        assert start_res.json()["is_trading_active"] is True

        stop_res = await client.post(
            "/api/trading/emergency-stop",
            headers=headers,
            json={"reason": "Integration test emergency stop"},
        )
        assert stop_res.status_code == 200
        assert stop_res.json()["emergency_stopped"] is True
        assert stop_res.json()["state"] == "HALTED"

        # Reset recovery (admin only)
        rec_res = await client.post("/api/trading/reset-recovery", headers=headers)
        assert rec_res.status_code == 200
        assert rec_res.json()["survival_state"] == "RECOVERY"

        # Deposit simulated PAPER capital
        dep_res = await client.post(
            "/api/portfolio/deposit",
            headers=headers,
            json={"amount": "99.00", "asset": "USDT", "note": "Test deposit"},
        )
        assert dep_res.status_code == 200
        assert dep_res.json()["is_simulated"] is True
        assert dep_res.json()["available_balance"] == 100.0
        assert dep_res.json()["total_equity"] == 100.0


@pytest.mark.asyncio
async def test_paper_execution_partial_fill_and_portfolio_reconciliation(db_manager, mock_exchange):
    async with db_manager.session() as session:
        repo = TradingRepository(session)
        account = await repo.get_or_create_default_account(initial_capital=Decimal("200.00"))
        rules = await mock_exchange.get_market_rules("BTC/USDT")
        ticker = await mock_exchange.fetch_ticker("BTC/USDT")
        ob = await mock_exchange.fetch_order_book("BTC/USDT")

        survival = SurvivalController()
        engine = ExecutionEngine(
            exchange=mock_exchange,
            survival=survival,
            simulator=PaperExecutionSimulator(),
        )
        req = OrderRequest(
            symbol="BTC/USDT",
            side="BUY",
            order_type="MARKET",
            quantity=Decimal("0.002000"),
            client_order_id=OrderManager.build_client_order_id("int"),
        )
        outcome = await engine.execute(
            session=session,
            account_id=account.id,
            risk_decision_id=None,
            request=req,
            rules=rules,
            ticker=ticker,
            order_book=ob,
            trading_mode=TradingMode.PAPER,
            live_authorized=False,
            strategy_name="trend_following",
            market_regime="BULLISH_TRENDING",
            force_fill_ratio=Decimal("0.50"),
        )
        assert outcome.executed is True
        assert outcome.result is not None
        assert outcome.result.status == "PARTIAL"
        assert outcome.result.filled_quantity == Decimal("0.001000")
        assert outcome.result.fee > Decimal("0")

        pm = PortfolioManager()
        await pm.open_position(
            session,
            account_id=account.id,
            symbol="BTC/USDT",
            strategy_name="trend_following",
            trading_mode="PAPER",
            quantity=outcome.result.filled_quantity,
            entry_price=outcome.result.average_price,
            entry_fee=outcome.result.fee,
            stop_loss=Decimal("60000"),
            take_profit=Decimal("70000"),
        )
        eq, snap = await pm.mark_to_market(
            session, account=account, latest_prices={"BTC/USDT": outcome.result.average_price}
        )
        assert snap.is_simulated is True
        assert eq.total_equity > Decimal("199.0")


@pytest.mark.asyncio
async def test_withdrawal_workflow_requires_realized_profit_and_human_approval(db_manager):
    async with db_manager.session() as session:
        repo = TradingRepository(session)
        account = await repo.get_or_create_default_account(initial_capital=Decimal("100.00"))
        await repo.upsert_balance(account.id, "USDT", Decimal("160.00"))
        # Record a closed winning position of +$60 realized profit
        session.add(
            Position(
                account_id=account.id,
                symbol="BTC/USDT",
                strategy_name="trend_following",
                trading_mode="PAPER",
                status="CLOSED",
                side="LONG",
                quantity=Decimal("0.001"),
                entry_price=Decimal("50000"),
                current_price=Decimal("110000"),
                realized_pnl=Decimal("60.00"),
            )
        )
        await session.flush()

        dest = await AddressManager.register_address(
            session,
            label="Hardware Cold Wallet",
            asset="USDT",
            network="ERC20",
            address="0x1234567890abcdef1234567890abcdef12345678",
            verified=True,
        )
        assert dest.is_verified is True

        wm = WithdrawalManager()
        wr = await wm.create_withdrawal_request(
            session,
            account=account,
            amount=Decimal("20.00"),
            destination_address=dest.address,
            network="ERC20",
        )
        assert wr.status == "PENDING_APPROVAL"

        # Cannot execute before human approval
        with pytest.raises(PermissionError):
            await wm.execute_approved_withdrawal(session, withdrawal_id=wr.id, executor="admin")

        # AI cannot approve its own request
        with pytest.raises(PermissionError):
            await ApprovalService.approve(session, approval_id=wr.approval_id, reviewer="agent")

        # Human admin approves -> execution succeeds
        await ApprovalService.approve(session, approval_id=wr.approval_id, reviewer="admin")
        executed = await wm.execute_approved_withdrawal(session, withdrawal_id=wr.id, executor="admin")
        assert executed.status == "EXECUTED"
        assert executed.tx_id is not None


@pytest.mark.asyncio
async def test_autonomous_operating_loop_end_to_end_cycle(db_manager, mock_exchange):
    settings = Settings(
        trading_mode=TradingMode.PAPER,
        initial_capital_usd=Decimal("1.00"),
    )
    loop = AutonomousTradingLoop(exchange=mock_exchange, settings=settings)
    async with db_manager.session() as session:
        repo = TradingRepository(session)
        acc = await repo.get_or_create_default_account(initial_capital=Decimal("1.00"))
        acc.is_trading_active = True
        await session.flush()

        res = await loop.run_cycle(session, allow_historical=False)
        assert res["survival_state"] in {"NORMAL", "CAUTION", "DEFENSIVE", "HALTED", "RECOVERY"}
        # $1 account must not force orders below exchange min_notional ($5)
        assert res["orders_submitted"] == 0


@pytest.mark.asyncio
async def test_exchange_credentials_live_read_only_and_confirmation_gate(db_manager, monkeypatch):
    from app.exchanges.base_exchange import ExchangeBalance
    from app.exchanges.ccxt_adapter import CCXTExchangeAdapter

    async def fake_fetch_balances(self):
        return {
            "USDT": ExchangeBalance(
                asset="USDT",
                free=Decimal("42.50"),
                used=Decimal("0"),
                total=Decimal("42.50"),
            ),
            "BTC": ExchangeBalance(
                asset="BTC",
                free=Decimal("0.0002"),
                used=Decimal("0"),
                total=Decimal("0.0002"),
            ),
        }

    async def fake_close(self):
        return None

    monkeypatch.setattr(CCXTExchangeAdapter, "fetch_balances", fake_fetch_balances)
    monkeypatch.setattr(CCXTExchangeAdapter, "close", fake_close)

    await bootstrap_initial_state()
    app = create_app()
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        tok_res = await client.post(
            "/api/auth/token",
            data={"username": "admin", "password": "admin-dev-password-change-me"},
        )
        token = tok_res.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        # 1. Connect Binance key -> safely enters LIVE_READ_ONLY and syncs balances
        cred_res = await client.post(
            "/api/exchange/credentials",
            headers=headers,
            json={
                "exchange_id": "binance",
                "api_key": "binance_test_public_key_1234",
                "api_secret": "binance_test_super_secret_5678",
                "sandbox": False,
                "enter_read_only": True,
            },
        )
        assert cred_res.status_code == 200
        body = cred_res.json()
        assert body["verified"] is True
        assert body["trading_mode"] == "LIVE_READ_ONLY"
        assert body["live_trading_authorized"] is False
        assert "5678" not in str(body)
        assert "1234" not in body["api_key_mask"] or "***" in body["api_key_mask"]
        assert body["sync"]["total_quote"] == 42.5

        dash = await client.get("/api/dashboard", headers=headers)
        assert dash.json()["account"]["trading_mode"] == "LIVE_READ_ONLY"
        assert dash.json()["account"]["is_simulated"] is False
        assert dash.json()["overview"]["available_balance"] == 42.5

        # 2. Cannot arm LIVE_TRADING without explicit checkbox + confirmation phrase
        bad_mode = await client.post(
            "/api/exchange/mode",
            headers=headers,
            json={
                "target_mode": "LIVE_TRADING",
                "risk_acknowledged": True,
                "confirmation_phrase": "wrong phrase",
            },
        )
        assert bad_mode.status_code == 400

        # 3. Arm LIVE_TRADING with explicit gate
        arm_res = await client.post(
            "/api/exchange/mode",
            headers=headers,
            json={
                "target_mode": "LIVE_TRADING",
                "risk_acknowledged": True,
                "confirmation_phrase": "ENABLE LIVE TRADING",
            },
        )
        assert arm_res.status_code == 200
        assert arm_res.json()["trading_mode"] == "LIVE_TRADING"
        assert arm_res.json()["live_trading_authorized"] is True
        assert arm_res.json()["is_trading_active"] is False

        # 4. Disarm back to LIVE_READ_ONLY
        disarm_res = await client.post(
            "/api/exchange/mode",
            headers=headers,
            json={"target_mode": "LIVE_READ_ONLY"},
        )
        assert disarm_res.status_code == 200
        assert disarm_res.json()["trading_mode"] == "LIVE_READ_ONLY"
        assert disarm_res.json()["live_trading_authorized"] is False


@pytest.mark.asyncio
async def test_futures_market_type_short_execution_and_margin_accounting(db_manager, mock_exchange):
    await bootstrap_initial_state()
    app = create_app()
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        tok_res = await client.post(
            "/api/auth/token",
            data={"username": "admin", "password": "admin-dev-password-change-me"},
        )
        headers = {"Authorization": f"Bearer {tok_res.json()['access_token']}"}

        # Reject Spot trading
        rej_spot = await client.post(
            "/api/exchange/market-type",
            headers=headers,
            json={"market_type": "SPOT", "leverage": 1, "futures_acknowledged": True},
        )
        assert rej_spot.status_code == 400

        # Reject leverage > 1x
        rej_lev = await client.post(
            "/api/exchange/market-type",
            headers=headers,
            json={"market_type": "FUTURES", "leverage": 2, "futures_acknowledged": True},
        )
        assert rej_lev.status_code == 400

        # Must acknowledge risk before confirming 1x FUTURES
        rej_ack = await client.post(
            "/api/exchange/market-type",
            headers=headers,
            json={"market_type": "FUTURES", "leverage": 1, "futures_acknowledged": False},
        )
        assert rej_ack.status_code == 400

        # Confirm isolated 1x FUTURES
        ok = await client.post(
            "/api/exchange/market-type",
            headers=headers,
            json={"market_type": "FUTURES", "leverage": 1, "futures_acknowledged": True},
        )
        assert ok.status_code == 200
        assert ok.json()["market_type"] == "FUTURES"
        assert ok.json()["leverage"] == 1
        assert ok.json()["margin_mode"] == "ISOLATED"

    # Execute & close a simulated SHORT position in 1x FUTURES mode (plus partial reduce-only validation)
    async with db_manager.session() as session:
        repo = TradingRepository(session)
        acc = await repo.get_or_create_default_account()
        await repo.upsert_balance(acc.id, "USDT", Decimal("100.00"))
        rules = await mock_exchange.get_market_rules("BTC/USDT")
        ticker = await mock_exchange.fetch_ticker("BTC/USDT")

        engine = ExecutionEngine(
            exchange=mock_exchange,
            survival=SurvivalController(),
            simulator=PaperExecutionSimulator(),
        )
        pm = PortfolioManager()

        short_req = OrderRequest(
            symbol="BTC/USDT",
            side="SELL",
            order_type="MARKET",
            quantity=Decimal("0.001000"),
            client_order_id=OrderManager.build_client_order_id("short"),
        )
        outcome = await engine.execute(
            session=session,
            account_id=acc.id,
            risk_decision_id=None,
            request=short_req,
            rules=rules,
            ticker=ticker,
            order_book=None,
            trading_mode=TradingMode.PAPER,
            live_authorized=False,
            strategy_name="momentum",
            market_regime="BEARISH_TRENDING",
            market_type="FUTURES",
            leverage=1,
        )
        assert outcome.executed is True
        entry_p = outcome.result.average_price
        margin = outcome.result.filled_quantity * entry_p
        pos = await pm.open_position(
            session,
            account_id=acc.id,
            symbol="BTC/USDT",
            strategy_name="momentum",
            trading_mode="PAPER",
            quantity=outcome.result.filled_quantity,
            entry_price=entry_p,
            entry_fee=outcome.result.fee,
            stop_loss=entry_p * Decimal("1.006"),
            take_profit=entry_p * Decimal("0.985"),
            side="SHORT",
            market_type="FUTURES",
            leverage=1,
            margin_used=margin,
        )

        # Partial reduce-only close below min_notional must still execute so profit-booking works on small sizes
        part_qty = Decimal("0.000200")
        part_req = OrderRequest(
            symbol="BTC/USDT",
            side="BUY",
            order_type="MARKET",
            quantity=part_qty,
            client_order_id=OrderManager.build_client_order_id("partbook"),
            reduce_only=True,
        )
        part_outcome = await engine.execute(
            session=session,
            account_id=acc.id,
            risk_decision_id=None,
            request=part_req,
            rules=rules,
            ticker=ticker,
            order_book=None,
            trading_mode=TradingMode.PAPER,
            live_authorized=False,
            strategy_name="momentum",
            market_regime="MANUAL_EXIT",
            market_type="FUTURES",
            leverage=1,
            closing_position=pos,
        )
        assert part_outcome.executed is True
        pos.quantity = pos.quantity - part_qty

        realized = await pm.close_position(
            session,
            position=pos,
            exit_price=entry_p * Decimal("0.98"),
            exit_fee=Decimal("0.05"),
        )
        assert realized > 0

        # Regression: legacy PAPER/SPOT position with missing BTC base balance must still close cleanly
        # even if the account later switched to LIVE_TRADING & FUTURES.
        acc.trading_mode = TradingMode.LIVE_TRADING.value
        acc.live_trading_authorized = True
        acc.market_type = "FUTURES"
        acc.leverage = 1
        legacy_spot = await pm.open_position(
            session,
            account_id=acc.id,
            symbol="BTC/USDT",
            strategy_name="trend_following",
            trading_mode="PAPER",
            quantity=Decimal("0.003020000"),
            entry_price=entry_p,
            entry_fee=Decimal("0.10"),
            stop_loss=entry_p * Decimal("0.99"),
            take_profit=entry_p * Decimal("1.02"),
            side="LONG",
            market_type="SPOT",
            leverage=1,
            margin_used=Decimal("0"),
        )
        await session.flush()
        legacy_close_req = OrderRequest(
            symbol="BTC/USDT",
            side="SELL",
            order_type="MARKET",
            quantity=legacy_spot.quantity,
            client_order_id=OrderManager.build_client_order_id("legacyclose"),
            reduce_only=False,
        )
        legacy_outcome = await engine.execute(
            session=session,
            account_id=acc.id,
            risk_decision_id=None,
            request=legacy_close_req,
            rules=rules,
            ticker=ticker,
            order_book=None,
            trading_mode=TradingMode(legacy_spot.trading_mode),
            live_authorized=False,
            strategy_name=legacy_spot.strategy_name,
            market_regime="MANUAL_EXIT",
            market_type=legacy_spot.market_type,
            leverage=legacy_spot.leverage,
            closing_position=legacy_spot,
        )
        assert legacy_outcome.executed is True
