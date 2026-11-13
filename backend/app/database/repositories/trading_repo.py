"""Repository layer for persistence of accounts, market data, signals, orders, and portfolio."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    Account,
    AgentEvent,
    Approval,
    Balance,
    DestinationAddress,
    MarketCandle,
    Order,
    PortfolioSnapshot,
    Position,
    RiskDecision,
    Strategy,
    StrategyMetric,
    StrategyVersion,
    Trade,
    TradeSignal,
    User,
    WithdrawalRequest,
)


class TradingRepository:
    """Async repository encapsulating database operations."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_or_create_default_account(
        self,
        *,
        name: str = "Primary Survival Account",
        exchange: str = "binance",
        trading_mode: str = "PAPER",
        initial_capital: Decimal = Decimal("1.00"),
        quote_currency: str = "USDT",
    ) -> Account:
        stmt = select(Account).order_by(Account.created_at.asc()).limit(1)
        result = await self.session.execute(stmt)
        account = result.scalar_one_or_none()
        if account is not None:
            # Futures-only policy: always USD-M Futures at 1x leverage (isolated)
            if account.market_type != "FUTURES" or account.leverage != 1:
                account.market_type = "FUTURES"
                account.leverage = 1
                await self.session.flush()
            return account

        account = Account(
            name=name,
            exchange=exchange,
            market_type="FUTURES",
            leverage=1,
            trading_mode=trading_mode,
            initial_capital=initial_capital,
            day_start_equity=initial_capital,
            day_start_date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            peak_equity=initial_capital,
            quote_currency=quote_currency,
            is_trading_active=False,
            emergency_stopped=False,
            live_trading_authorized=False,
        )
        self.session.add(account)
        await self.session.flush()

        quote_balance = Balance(
            account_id=account.id,
            asset=quote_currency,
            available=initial_capital,
            locked=Decimal("0"),
            total=initial_capital,
        )
        self.session.add(quote_balance)
        await self.session.flush()
        return account

    async def get_balances(self, account_id: uuid.UUID) -> list[Balance]:
        stmt = select(Balance).where(Balance.account_id == account_id).order_by(Balance.asset.asc())
        res = await self.session.execute(stmt)
        return list(res.scalars().all())

    async def upsert_balance(
        self,
        account_id: uuid.UUID,
        asset: str,
        available: Decimal,
        locked: Decimal = Decimal("0"),
        total: Decimal | None = None,
    ) -> Balance:
        stmt = select(Balance).where(Balance.account_id == account_id, Balance.asset == asset)
        res = await self.session.execute(stmt)
        bal = res.scalar_one_or_none()
        computed_total = total if total is not None else available + locked
        now = datetime.now(timezone.utc)
        if bal is None:
            bal = Balance(
                account_id=account_id,
                asset=asset,
                available=available,
                locked=locked,
                total=computed_total,
                reconciled_at=now,
            )
            self.session.add(bal)
        else:
            bal.available = available
            bal.locked = locked
            bal.total = computed_total
            bal.reconciled_at = now
        await self.session.flush()
        return bal

    async def save_candles(
        self,
        exchange: str,
        symbol: str,
        timeframe: str,
        candles: list[dict[str, Any]],
    ) -> int:
        if not candles:
            return 0
        timestamps = [c["timestamp"] for c in candles]
        stmt = select(MarketCandle.timestamp).where(
            MarketCandle.exchange == exchange,
            MarketCandle.symbol == symbol,
            MarketCandle.timeframe == timeframe,
            MarketCandle.timestamp.in_(timestamps),
        )
        res = await self.session.execute(stmt)
        existing_ts = {
            ts.astimezone(timezone.utc) if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
            for ts in res.scalars().all()
        }

        from sqlalchemy.dialects.sqlite import insert as sqlite_insert

        now_utc = datetime.now(timezone.utc)
        rows: list[dict[str, Any]] = []
        for c in candles:
            ts = c["timestamp"]
            ts_utc = ts.astimezone(timezone.utc) if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
            if ts_utc in existing_ts:
                continue
            existing_ts.add(ts_utc)
            rows.append(
                {
                    "id": uuid.uuid4(),
                    "exchange": exchange,
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "timestamp": ts_utc,
                    "open": Decimal(str(c["open"])),
                    "high": Decimal(str(c["high"])),
                    "low": Decimal(str(c["low"])),
                    "close": Decimal(str(c["close"])),
                    "volume": Decimal(str(c["volume"])),
                    "is_validated": bool(c.get("is_validated", True)),
                    "created_at": now_utc,
                }
            )
        if not rows:
            return 0
        bind = self.session.get_bind()
        if bind is not None and bind.dialect.name == "sqlite":
            stmt_ins = (
                sqlite_insert(MarketCandle)
                .values(rows)
                .on_conflict_do_nothing(
                    index_elements=["exchange", "symbol", "timeframe", "timestamp"]
                )
            )
            await self.session.execute(stmt_ins)
        else:
            self.session.add_all([MarketCandle(**r) for r in rows])
        await self.session.flush()
        return len(rows)

    async def get_candles(
        self,
        exchange: str,
        symbol: str,
        timeframe: str,
        limit: int = 200,
    ) -> list[MarketCandle]:
        stmt = (
            select(MarketCandle)
            .where(
                MarketCandle.exchange == exchange,
                MarketCandle.symbol == symbol,
                MarketCandle.timeframe == timeframe,
            )
            .order_by(desc(MarketCandle.timestamp))
            .limit(limit)
        )
        res = await self.session.execute(stmt)
        rows = list(res.scalars().all())
        rows.reverse()
        return rows

    async def log_agent_event(
        self,
        *,
        event_type: str,
        component: str,
        message: str,
        severity: str = "INFO",
        payload: dict[str, Any] | None = None,
    ) -> AgentEvent:
        ev = AgentEvent(
            event_type=event_type,
            severity=severity,
            component=component,
            message=message,
            payload=payload or {},
        )
        self.session.add(ev)
        await self.session.flush()
        return ev

    async def get_recent_events(self, limit: int = 100) -> list[AgentEvent]:
        stmt = select(AgentEvent).order_by(desc(AgentEvent.created_at)).limit(limit)
        res = await self.session.execute(stmt)
        return list(res.scalars().all())

    async def list_strategies(self) -> list[Strategy]:
        stmt = select(Strategy).order_by(Strategy.name.asc())
        res = await self.session.execute(stmt)
        return list(res.scalars().all())

    async def get_open_positions(self, account_id: uuid.UUID) -> list[Position]:
        stmt = (
            select(Position)
            .where(Position.account_id == account_id, Position.status == "OPEN")
            .order_by(desc(Position.opened_at))
        )
        res = await self.session.execute(stmt)
        return list(res.scalars().all())

    async def get_recent_orders(self, account_id: uuid.UUID, limit: int = 50) -> list[Order]:
        stmt = (
            select(Order)
            .where(Order.account_id == account_id)
            .order_by(desc(Order.created_at))
            .limit(limit)
        )
        res = await self.session.execute(stmt)
        return list(res.scalars().all())

    async def get_recent_trades(self, account_id: uuid.UUID, limit: int = 100) -> list[Trade]:
        stmt = (
            select(Trade)
            .where(Trade.account_id == account_id)
            .order_by(desc(Trade.executed_at))
            .limit(limit)
        )
        res = await self.session.execute(stmt)
        return list(res.scalars().all())

    async def get_latest_snapshot(self, account_id: uuid.UUID) -> PortfolioSnapshot | None:
        stmt = (
            select(PortfolioSnapshot)
            .where(PortfolioSnapshot.account_id == account_id)
            .order_by(desc(PortfolioSnapshot.recorded_at))
            .limit(1)
        )
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()

    async def get_snapshots(self, account_id: uuid.UUID, limit: int = 100) -> list[PortfolioSnapshot]:
        stmt = (
            select(PortfolioSnapshot)
            .where(PortfolioSnapshot.account_id == account_id)
            .order_by(desc(PortfolioSnapshot.recorded_at))
            .limit(limit)
        )
        res = await self.session.execute(stmt)
        rows = list(res.scalars().all())
        rows.reverse()
        return rows

    async def list_approvals(self, status: str | None = None, limit: int = 50) -> list[Approval]:
        stmt = select(Approval)
        if status:
            stmt = stmt.where(Approval.status == status)
        stmt = stmt.order_by(desc(Approval.created_at)).limit(limit)
        res = await self.session.execute(stmt)
        return list(res.scalars().all())

    async def list_withdrawals(self, account_id: uuid.UUID, limit: int = 50) -> list[WithdrawalRequest]:
        stmt = (
            select(WithdrawalRequest)
            .where(WithdrawalRequest.account_id == account_id)
            .order_by(desc(WithdrawalRequest.created_at))
            .limit(limit)
        )
        res = await self.session.execute(stmt)
        return list(res.scalars().all())

    async def list_destination_addresses(self) -> list[DestinationAddress]:
        stmt = select(DestinationAddress).order_by(DestinationAddress.created_at.desc())
        res = await self.session.execute(stmt)
        return list(res.scalars().all())

    async def get_user_by_username(self, username: str) -> User | None:
        stmt = select(User).where(User.username == username)
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()

    async def get_strategy_metrics(self, limit: int = 100) -> list[StrategyMetric]:
        stmt = select(StrategyMetric).order_by(desc(StrategyMetric.recorded_at)).limit(limit)
        res = await self.session.execute(stmt)
        return list(res.scalars().all())

    async def get_strategy_versions(self, limit: int = 100) -> list[StrategyVersion]:
        stmt = select(StrategyVersion).order_by(desc(StrategyVersion.created_at)).limit(limit)
        res = await self.session.execute(stmt)
        return list(res.scalars().all())

    async def get_recent_risk_decisions(self, limit: int = 50) -> list[RiskDecision]:
        stmt = select(RiskDecision).order_by(desc(RiskDecision.created_at)).limit(limit)
        res = await self.session.execute(stmt)
        return list(res.scalars().all())

    async def get_recent_signals(self, limit: int = 50) -> list[TradeSignal]:
        stmt = select(TradeSignal).order_by(desc(TradeSignal.created_at)).limit(limit)
        res = await self.session.execute(stmt)
        return list(res.scalars().all())

    async def get_closed_positions(self, account_id: uuid.UUID, limit: int = 500) -> list[Position]:
        stmt = (
            select(Position)
            .where(Position.account_id == account_id, Position.status == "CLOSED")
            .order_by(desc(Position.closed_at))
            .limit(limit)
        )
        res = await self.session.execute(stmt)
        return list(res.scalars().all())

    async def get_strategy_regime_realized_stats(
        self,
        account_id: uuid.UUID,
        *,
        limit: int = 500,
    ) -> dict[tuple[str, str], dict[str, Any]]:
        """Aggregate closed trade count, wins, losses, avg win/loss %, and net expectancy by (strategy, regime)."""
        rows = await self.get_closed_positions(account_id, limit=limit)
        buckets: dict[tuple[str, str], list[float]] = {}
        for pos in rows:
            if pos.net_pnl_pct is not None:
                ret_pct = float(pos.net_pnl_pct)
            else:
                denom = float(pos.margin_used or (pos.quantity * pos.entry_price) or Decimal("0"))
                if denom <= 0:
                    continue
                ret_pct = float(pos.realized_pnl) / denom
            regime_key = str(pos.market_regime or "ALL").upper()
            for key in [(pos.strategy_name, regime_key), (pos.strategy_name, "ALL")]:
                buckets.setdefault(key, []).append(ret_pct)

        summary: dict[tuple[str, str], dict[str, Any]] = {}
        for key, rets in buckets.items():
            wins = [r for r in rets if r > 0]
            losses = [abs(r) for r in rets if r <= 0]
            count = len(rets)
            net_exp = sum(rets) / count if count > 0 else 0.0
            summary[key] = {
                "trade_count": count,
                "wins": len(wins),
                "losses": len(losses),
                "win_rate": round(len(wins) / count, 4) if count > 0 else 0.0,
                "avg_win_pct": (sum(wins) / len(wins)) if wins else 0.0,
                "avg_loss_pct": (sum(losses) / len(losses)) if losses else 0.0,
                "net_expectancy_pct": round(net_exp, 6),
            }
        return summary
