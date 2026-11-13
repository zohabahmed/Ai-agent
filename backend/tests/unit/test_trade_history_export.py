"""Unit & integration tests for Trade History Download / Export (CSV & JSON)."""

from __future__ import annotations

import csv
from datetime import datetime, timezone
from decimal import Decimal
import io
import json
from typing import Any
import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.dependencies import get_db_session
from app.config import TradingMode
from app.database.models import Position, Trade, User
from app.database.repositories.trading_repo import TradingRepository
from app.main import app


@pytest.mark.asyncio
async def test_export_closed_trades_history_csv(db_manager: Any) -> None:
    async with db_manager.session() as session:
        repo = TradingRepository(session)
        acct = await repo.get_or_create_default_account(
            exchange="binance", trading_mode="LIVE_TRADING", initial_capital=Decimal("100.00")
        )
        acct.market_type = "FUTURES"

        user = User(
            username="admin",
            hashed_password="hash",
            role="admin",
            is_active=True,
        )
        session.add(user)

        opened_dt = datetime(2026, 11, 13, 10, 0, 0, tzinfo=timezone.utc)
        closed_dt = datetime(2026, 11, 13, 10, 45, 0, tzinfo=timezone.utc)

        pos1 = Position(
            account_id=acct.id,
            symbol="BNB/USDT",
            strategy_name="volatility_breakout",
            trading_mode="LIVE_TRADING",
            market_type="FUTURES",
            leverage=4,
            margin_used=Decimal("3.16"),
            status="CLOSED",
            side="SHORT",
            quantity=Decimal("0.0100"),
            entry_price=Decimal("631.72"),
            current_price=Decimal("621.50"),
            stop_loss=Decimal("638.00"),
            take_profit=Decimal("618.00"),
            exit_reason="TAKE_PROFIT",
            net_pnl_pct=Decimal("0.0161"),
            market_regime="BEARISH_TRENDING",
            entry_fee=Decimal("0.0031"),
            unrealized_pnl=Decimal("0"),
            realized_pnl=Decimal("0.1022"),
            opened_at=opened_dt,
            closed_at=closed_dt,
        )
        pos2 = Position(
            account_id=acct.id,
            symbol="ARB/USDT",
            strategy_name="momentum",
            trading_mode="LIVE_TRADING",
            market_type="FUTURES",
            leverage=4,
            margin_used=Decimal("1.77"),
            status="CLOSED",
            side="SHORT",
            quantity=Decimal("48.9000"),
            entry_price=Decimal("0.14532"),
            current_price=Decimal("0.14462"),
            stop_loss=Decimal("0.14800"),
            take_profit=Decimal("0.14200"),
            exit_reason="MANUAL_CLOSE",
            net_pnl_pct=Decimal("0.0048"),
            market_regime="BEARISH_TRENDING",
            entry_fee=Decimal("0.0035"),
            unrealized_pnl=Decimal("0"),
            realized_pnl=Decimal("0.0342"),
            opened_at=opened_dt,
            closed_at=closed_dt,
        )
        pos_open = Position(
            account_id=acct.id,
            symbol="BTC/USDT",
            strategy_name="trend_following",
            trading_mode="LIVE_TRADING",
            market_type="FUTURES",
            leverage=1,
            margin_used=Decimal("5.00"),
            status="OPEN",
            side="LONG",
            quantity=Decimal("0.001"),
            entry_price=Decimal("70000"),
            current_price=Decimal("70500"),
            opened_at=opened_dt,
        )
        session.add_all([pos1, pos2, pos_open])
        await session.flush()

    async def _get_test_session():
        async with db_manager.session() as s:
            yield s

    app.dependency_overrides[get_db_session] = _get_test_session

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            headers = {"Authorization": "Bearer direct-operator"}

            # Test 1: Closed trades CSV download
            res = await client.get("/api/trading/trades/export?format=csv&scope=closed", headers=headers)
            assert res.status_code == 200
            assert "text/csv" in res.headers["content-type"]
            assert "attachment; filename=" in res.headers["content-disposition"]
            assert res.headers["content-disposition"].endswith('.csv"')

            reader = csv.DictReader(io.StringIO(res.text))
            rows = list(reader)
            # Must only contain the 2 closed trades, excluding the 1 open trade
            assert len(rows) == 2

            bnb_row = next(r for r in rows if r["Pair"] == "BNB/USDT")
            assert bnb_row["Coin"] == "BNBUSDT"
            assert bnb_row["Side"] == "SHORT"
            assert bnb_row["Status"] == "CLOSED"
            assert bnb_row["Leverage"] == "4x"
            assert float(bnb_row["Entry Price ($)"]) == 631.72
            assert float(bnb_row["Exit Price ($)"]) == 621.5
            assert float(bnb_row["Realized Profit/Loss ($)"]) == 0.1022
            assert float(bnb_row["ROI (%)"]) > 0
            assert bnb_row["Strategy"] == "volatility_breakout"
            assert bnb_row["Exit Reason"] == "TAKE_PROFIT"
            assert float(bnb_row["Hold Duration (Minutes)"]) == 45.0

            # Test 2: Closed trades JSON download
            res_json = await client.get("/api/trading/trades/export?format=json&scope=closed", headers=headers)
            assert res_json.status_code == 200
            assert "application/json" in res_json.headers["content-type"]
            data = res_json.json()
            assert data["count"] == 2
            assert len(data["trades"]) == 2

            # Test 3: Export alias /api/trades/export also works
            res_alias = await client.get("/api/trades/export?format=csv", headers=headers)
            assert res_alias.status_code == 200

            # Test 4: Scope all includes open positions too
            res_all = await client.get("/api/trading/trades/export?format=json&scope=all", headers=headers)
            assert res_all.status_code == 200
            data_all = res_all.json()
            assert data_all["count"] == 3
    finally:
        app.dependency_overrides.pop(get_db_session, None)


@pytest.mark.asyncio
async def test_export_empty_trades_history_returns_valid_headers(db_manager: Any) -> None:
    async def _get_test_session():
        async with db_manager.session() as s:
            yield s

    app.dependency_overrides[get_db_session] = _get_test_session

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            headers = {"Authorization": "Bearer direct-operator"}
            res = await client.get("/api/trading/trades/export?format=csv&scope=closed", headers=headers)
            assert res.status_code == 200
            reader = csv.DictReader(io.StringIO(res.text))
            assert "Trade ID" in (reader.fieldnames or [])
            assert "Realized Profit/Loss ($)" in (reader.fieldnames or [])
            assert "Coin" in (reader.fieldnames or [])
            rows = list(reader)
            assert len(rows) == 0
    finally:
        app.dependency_overrides.pop(get_db_session, None)
