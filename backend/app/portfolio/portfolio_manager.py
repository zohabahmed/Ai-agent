"""Portfolio Manager updating positions, realized/unrealized P&L, and snapshots."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, TradingMode, get_settings
from app.database.models import Account, PortfolioSnapshot, Position, Trade
from app.database.repositories.trading_repo import TradingRepository
from app.portfolio.balance_manager import BalanceManager
from app.portfolio.equity_calculator import EquityCalculator, EquitySummary
from app.portfolio.profit_manager import ProfitManager
from app.risk.daily_baseline import sync_utc_day_baseline


class PortfolioManager:
    """Maintains accurate spot accounting and creates portfolio snapshots."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    async def open_position(
        self,
        session: AsyncSession,
        *,
        account_id: uuid.UUID,
        symbol: str,
        strategy_name: str,
        trading_mode: str,
        quantity: Decimal,
        entry_price: Decimal,
        entry_fee: Decimal,
        stop_loss: Decimal | None,
        take_profit: Decimal | None,
        side: str = "LONG",
        market_type: str = "SPOT",
        leverage: int = 1,
        margin_used: Decimal = Decimal("0"),
        initial_stop_distance_pct: Decimal | None = None,
        atr_at_entry: Decimal | None = None,
        sl_order_id: str | None = None,
        tp_order_id: str | None = None,
        market_regime: str | None = None,
    ) -> Position:
        computed_stop_dist = initial_stop_distance_pct
        if computed_stop_dist is None and stop_loss is not None and entry_price > 0:
            computed_stop_dist = abs(entry_price - stop_loss) / entry_price
        pos = Position(
            account_id=account_id,
            symbol=symbol,
            strategy_name=strategy_name,
            trading_mode=trading_mode,
            market_type=market_type.upper(),
            leverage=max(1, min(int(leverage), 5)) if market_type.upper() == "FUTURES" else 1,
            margin_used=margin_used,
            status="OPEN",
            side=side.upper(),
            quantity=quantity,
            entry_price=entry_price,
            current_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            initial_stop_distance_pct=computed_stop_dist,
            atr_at_entry=atr_at_entry,
            sl_order_id=sl_order_id,
            tp_order_id=tp_order_id,
            market_regime=market_regime,
            entry_fee=entry_fee,
            unrealized_pnl=-entry_fee,
            realized_pnl=Decimal("0"),
        )
        session.add(pos)
        await session.flush()
        return pos

    async def close_position(
        self,
        session: AsyncSession,
        *,
        position: Position,
        exit_price: Decimal,
        exit_fee: Decimal,
        exit_reason: str | None = None,
    ) -> Decimal:
        gross_pnl = (
            (position.entry_price - exit_price) * position.quantity
            if position.side.upper() == "SHORT"
            else (exit_price - position.entry_price) * position.quantity
        )
        net_realized = gross_pnl - position.entry_fee - exit_fee
        denom = position.margin_used if position.margin_used and position.margin_used > 0 else (position.quantity * position.entry_price)
        net_pct = (net_realized / denom).quantize(Decimal("0.00000001")) if denom > 0 else Decimal("0")
        position.current_price = exit_price
        position.realized_pnl = net_realized.quantize(Decimal("0.00000001"))
        position.net_pnl_pct = net_pct
        if exit_reason:
            position.exit_reason = exit_reason
        position.unrealized_pnl = Decimal("0")
        position.status = "CLOSED"
        position.closed_at = datetime.now(timezone.utc)
        await session.flush()
        return position.realized_pnl

    async def mark_to_market(
        self,
        session: AsyncSession,
        *,
        account: Account,
        latest_prices: dict[str, Decimal],
    ) -> tuple[EquitySummary, PortfolioSnapshot]:
        repo = TradingRepository(session)
        bal_mgr = BalanceManager(session)
        avail_quote = await bal_mgr.get_quote_available(account.id, account.quote_currency)
        locked_quote = await bal_mgr.get_quote_locked(account.id, account.quote_currency)

        open_positions = await repo.get_open_positions(account.id)
        pos_tuples: list[tuple[Decimal, Decimal, Decimal, Decimal, str, str, Decimal]] = []
        for pos in open_positions:
            curr_p = latest_prices.get(pos.symbol, pos.current_price)
            pos.current_price = curr_p
            raw_pnl = (
                (pos.entry_price - curr_p) * pos.quantity
                if pos.side.upper() == "SHORT"
                else (curr_p - pos.entry_price) * pos.quantity
            )
            pos.unrealized_pnl = (raw_pnl - pos.entry_fee).quantize(Decimal("0.00000001"))
            pos_tuples.append(
                (pos.quantity, pos.entry_price, curr_p, pos.entry_fee, pos.side, pos.market_type, pos.margin_used)
            )

        # Aggregate realized PnL from closed positions and total fees from trades
        realized_stmt = select(func.coalesce(func.sum(Position.realized_pnl), 0)).where(
            Position.account_id == account.id,
            Position.status == "CLOSED",
        )
        realized_res = await session.execute(realized_stmt)
        realized_pnl = Decimal(str(realized_res.scalar_one() or 0))

        fee_stmt = select(func.coalesce(func.sum(Trade.fee), 0)).where(Trade.account_id == account.id)
        fee_res = await session.execute(fee_stmt)
        total_fees = Decimal(str(fee_res.scalar_one() or 0))

        equity = EquityCalculator.calculate_mixed(
            initial_capital=account.initial_capital,
            available_quote=avail_quote,
            locked_quote=locked_quote,
            positions=pos_tuples,
            realized_pnl=realized_pnl,
            total_fees=total_fees,
        )

        if equity.total_equity > account.peak_equity:
            account.peak_equity = equity.total_equity
        peak = max(account.peak_equity, equity.total_equity, Decimal("0.000001"))
        drawdown_pct = max(Decimal("0"), (peak - equity.total_equity) / peak)

        sync_utc_day_baseline(account, equity.total_equity)
        day_base = account.day_start_equity if account.day_start_equity > 0 else account.initial_capital
        daily_pnl_pct = (
            ((equity.total_equity - day_base) / day_base) * Decimal("100")
            if day_base > 0
            else Decimal("0")
        )

        profit_summary = ProfitManager.calculate_withdrawable(
            realized_net_profit=realized_pnl,
            unrealized_pnl=equity.unrealized_pnl,
            available_cash=avail_quote,
            min_reserve_capital=self.settings.min_reserve_capital_usd,
            profit_allocation_pct=self.settings.profit_allocation_pct,
        )

        is_simulated = account.trading_mode in {TradingMode.PAPER.value, TradingMode.BACKTEST.value}
        snapshot = PortfolioSnapshot(
            account_id=account.id,
            trading_mode=account.trading_mode,
            initial_capital=account.initial_capital,
            available_balance=equity.available_balance,
            locked_balance=equity.locked_balance,
            open_position_value=equity.open_position_value,
            total_equity=equity.total_equity,
            realized_pnl=equity.realized_pnl,
            unrealized_pnl=equity.unrealized_pnl,
            total_fees=equity.total_fees,
            drawdown_pct=drawdown_pct.quantize(Decimal("0.000001")),
            daily_pnl_pct=daily_pnl_pct.quantize(Decimal("0.0001")),
            withdrawable_amount=profit_summary.withdrawable_profit,
            is_simulated=is_simulated,
        )
        session.add(snapshot)
        await session.flush()
        return equity, snapshot
