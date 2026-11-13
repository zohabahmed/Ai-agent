"""Portfolio summary, balances, open positions, equity curve, and simulated paper capital management endpoints."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import (
    get_current_user,
    get_survival_controller,
    rate_limit_guard,
    require_role,
)
from app.config import TradingMode, get_settings
from app.database.models import PortfolioSnapshot, User
from app.database.repositories.trading_repo import TradingRepository
from app.database.session import get_db_session
from app.portfolio.balance_manager import BalanceManager
from app.portfolio.equity_calculator import EquityCalculator
from app.portfolio.profit_manager import ProfitManager
from app.risk.survival_controller import SurvivalController, SurvivalState
from app.security.audit_logger import AuditLogger
from app.security.authorization import Role

router = APIRouter(tags=["portfolio"])


class DepositBody(BaseModel):
    amount: Decimal = Field(gt=Decimal("0"), le=Decimal("1000000"))
    asset: str = Field(default="USDT", max_length=16)
    note: str = Field(default="Simulated PAPER deposit", max_length=255)


class PaperResetBody(BaseModel):
    initial_capital: Decimal = Field(default=Decimal("1.00"), ge=Decimal("1.00"), le=Decimal("1000000"))
    switch_to_paper: bool = Field(default=True)


@router.get("/portfolio", dependencies=[Depends(rate_limit_guard)])
async def get_portfolio(
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict[str, Any]:
    settings = get_settings()
    repo = TradingRepository(session)
    account = await repo.get_or_create_default_account(
        exchange=settings.exchange_id,
        trading_mode=settings.trading_mode.value,
        initial_capital=settings.initial_capital_usd,
        quote_currency=settings.quote_currency,
    )
    bal_mgr = BalanceManager(session)
    balances = await bal_mgr.get_all(account.id)
    avail = await bal_mgr.get_quote_available(account.id, account.quote_currency)
    locked = await bal_mgr.get_quote_locked(account.id, account.quote_currency)
    open_positions = await repo.get_open_positions(account.id)
    trades = await repo.get_recent_trades(account.id, limit=200)
    snapshots = await repo.get_snapshots(account.id, limit=120)
    latest_snap = snapshots[-1] if snapshots else None

    pos_tuples = [
        (p.quantity, p.entry_price, p.current_price, p.entry_fee, p.side, p.market_type, p.margin_used)
        for p in open_positions
    ]
    realized = latest_snap.realized_pnl if latest_snap else Decimal("0")
    total_fees = sum((t.fee for t in trades), Decimal("0"))
    eq = EquityCalculator.calculate_mixed(
        initial_capital=account.initial_capital,
        available_quote=avail,
        locked_quote=locked,
        positions=pos_tuples,
        realized_pnl=realized,
        total_fees=total_fees,
    )
    profit = ProfitManager.calculate_withdrawable(
        realized_net_profit=eq.realized_pnl,
        unrealized_pnl=eq.unrealized_pnl,
        available_cash=eq.available_balance,
        min_reserve_capital=settings.min_reserve_capital_usd,
        profit_allocation_pct=settings.profit_allocation_pct,
    )
    peak = max(account.peak_equity, eq.total_equity, Decimal("0.000001"))
    drawdown_pct = max(Decimal("0"), (peak - eq.total_equity) / peak)

    return {
        "account_id": str(account.id),
        "trading_mode": account.trading_mode,
        "market_type": account.market_type,
        "leverage": account.leverage,
        "is_simulated": account.trading_mode in {TradingMode.PAPER.value, TradingMode.BACKTEST.value},
        "equity": {
            "initial_capital": float(eq.initial_capital),
            "available_balance": float(eq.available_balance),
            "locked_balance": float(eq.locked_balance),
            "open_position_value": float(eq.open_position_value),
            "total_equity": float(eq.total_equity),
            "realized_pnl": float(eq.realized_pnl),
            "unrealized_pnl": float(eq.unrealized_pnl),
            "total_fees": float(eq.total_fees),
            "net_return_pct": float(eq.net_return_pct),
            "drawdown_pct": float(drawdown_pct),
        },
        "profit": {
            "realized_net_profit": float(profit.realized_net_profit),
            "unrealized_pnl": float(profit.unrealized_pnl),
            "reserve_capital_required": float(profit.reserve_capital_required),
            "withdrawable_profit": float(profit.withdrawable_profit),
            "retained_growth_capital": float(
                max(Decimal("0"), profit.realized_net_profit - profit.withdrawable_profit)
            ),
        },
        "balances": [
            {
                "asset": b.asset,
                "available": float(b.available),
                "locked": float(b.locked),
                "total": float(b.total),
                "reconciled_at": b.reconciled_at.isoformat(),
            }
            for b in balances
        ],
        "open_positions": [
            {
                "id": str(p.id),
                "symbol": p.symbol,
                "strategy_name": p.strategy_name,
                "side": p.side,
                "market_type": p.market_type,
                "leverage": p.leverage,
                "margin_used": float(p.margin_used),
                "quantity": float(p.quantity),
                "entry_price": float(p.entry_price),
                "current_price": float(p.current_price),
                "stop_loss": float(p.stop_loss) if p.stop_loss else None,
                "take_profit": float(p.take_profit) if p.take_profit else None,
                "unrealized_pnl": float(p.unrealized_pnl),
                "opened_at": p.opened_at.isoformat(),
            }
            for p in open_positions
        ],
        "equity_curve": [
            {
                "timestamp": s.recorded_at.isoformat(),
                "total_equity": float(s.total_equity),
                "available_balance": float(s.available_balance),
                "open_position_value": float(s.open_position_value),
                "realized_pnl": float(s.realized_pnl),
                "unrealized_pnl": float(s.unrealized_pnl),
                "drawdown_pct": float(s.drawdown_pct),
                "is_simulated": s.is_simulated,
            }
            for s in snapshots
        ],
    }


@router.post("/portfolio/deposit", dependencies=[Depends(rate_limit_guard)])
async def deposit_simulated_capital(
    body: DepositBody,
    user: Annotated[User, Depends(require_role(Role.OPERATOR))],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict[str, Any]:
    """Credit virtual capital to the PAPER account for agent testing."""
    repo = TradingRepository(session)
    account = await repo.get_or_create_default_account()

    if account.trading_mode not in {TradingMode.PAPER.value, TradingMode.BACKTEST.value}:
        raise HTTPException(
            status_code=400,
            detail="Virtual deposits are only permitted in PAPER mode. Switch to PAPER mode or use Sync Live Balances.",
        )

    asset = body.asset.upper().strip()
    bal_mgr = BalanceManager(session)
    current_avail = await bal_mgr.get_quote_available(account.id, asset)
    current_locked = await bal_mgr.get_quote_locked(account.id, asset)
    new_avail = current_avail + body.amount

    await repo.upsert_balance(
        account_id=account.id,
        asset=asset,
        available=new_avail,
        locked=current_locked,
    )

    if asset == account.quote_currency:
        account.initial_capital = account.initial_capital + body.amount
        account.peak_equity = max(account.peak_equity, account.initial_capital)

    open_positions = await repo.get_open_positions(account.id)
    pos_tuples = [
        (p.quantity, p.entry_price, p.current_price, p.entry_fee, p.side, p.market_type, p.margin_used)
        for p in open_positions
    ]
    latest_snap = await repo.get_latest_snapshot(account.id)
    realized = latest_snap.realized_pnl if latest_snap else Decimal("0")
    eq = EquityCalculator.calculate_mixed(
        initial_capital=account.initial_capital,
        available_quote=new_avail,
        locked_quote=current_locked,
        positions=pos_tuples,
        realized_pnl=realized,
        total_fees=Decimal("0"),
    )

    snap = PortfolioSnapshot(
        account_id=account.id,
        trading_mode=account.trading_mode,
        initial_capital=account.initial_capital,
        available_balance=eq.available_balance,
        locked_balance=eq.locked_balance,
        open_position_value=eq.open_position_value,
        total_equity=eq.total_equity,
        realized_pnl=eq.realized_pnl,
        unrealized_pnl=eq.unrealized_pnl,
        total_fees=eq.total_fees,
        drawdown_pct=Decimal("0"),
        daily_pnl_pct=Decimal("0"),
        withdrawable_amount=Decimal("0"),
        is_simulated=True,
    )
    session.add(snap)
    await session.flush()

    await repo.log_agent_event(
        event_type="PAPER_CAPITAL_DEPOSIT",
        component="portfolio",
        severity="INFO",
        message=f"Operator {user.username} credited +${float(body.amount):.2f} {asset} to PAPER account ({body.note}).",
        payload={"amount": float(body.amount), "asset": asset, "total_equity": float(eq.total_equity)},
    )
    await AuditLogger.log(
        session,
        actor=user.username,
        action="PAPER_CAPITAL_DEPOSIT",
        resource_type="account",
        resource_id=str(account.id),
        details={"amount": str(body.amount), "asset": asset, "note": body.note},
    )

    return {
        "status": "deposited",
        "is_simulated": True,
        "deposited_amount": float(body.amount),
        "asset": asset,
        "available_balance": float(new_avail),
        "total_equity": float(eq.total_equity),
        "initial_capital": float(account.initial_capital),
    }


@router.post("/portfolio/reset-paper", dependencies=[Depends(rate_limit_guard)])
async def reset_paper_testing_account(
    body: PaperResetBody,
    user: Annotated[User, Depends(require_role(Role.OPERATOR))],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    survival: Annotated[SurvivalController, Depends(get_survival_controller)],
) -> dict[str, Any]:
    """Reset the Paper Trading Testing Lab to a clean starting capital and close open paper positions."""
    repo = TradingRepository(session)
    account = await repo.get_or_create_default_account()

    if body.switch_to_paper:
        account.trading_mode = TradingMode.PAPER.value
        account.live_trading_authorized = False

    if account.trading_mode not in {TradingMode.PAPER.value, TradingMode.BACKTEST.value}:
        raise HTTPException(
            status_code=400,
            detail="Cannot reset paper account while in LIVE mode unless switch_to_paper=true.",
        )

    open_positions = await repo.get_open_positions(account.id)
    now = datetime.now(timezone.utc)
    for pos in open_positions:
        pos.status = "CLOSED"
        pos.closed_at = now

    balances = await repo.get_balances(account.id)
    for bal in balances:
        if bal.asset != account.quote_currency:
            bal.available = Decimal("0")
            bal.locked = Decimal("0")
            bal.total = Decimal("0")

    await repo.upsert_balance(
        account_id=account.id,
        asset=account.quote_currency,
        available=body.initial_capital,
        locked=Decimal("0"),
    )

    account.initial_capital = body.initial_capital
    account.peak_equity = body.initial_capital
    account.emergency_stopped = False
    account.is_trading_active = True
    account.survival_state = "NORMAL"

    survival.emergency_stopped = False
    survival.emergency_stop_reason = None
    survival.state = SurvivalState.NORMAL

    snap = PortfolioSnapshot(
        account_id=account.id,
        trading_mode=account.trading_mode,
        initial_capital=body.initial_capital,
        available_balance=body.initial_capital,
        locked_balance=Decimal("0"),
        open_position_value=Decimal("0"),
        total_equity=body.initial_capital,
        realized_pnl=Decimal("0"),
        unrealized_pnl=Decimal("0"),
        total_fees=Decimal("0"),
        drawdown_pct=Decimal("0"),
        daily_pnl_pct=Decimal("0"),
        withdrawable_amount=Decimal("0"),
        is_simulated=True,
    )
    session.add(snap)
    await session.flush()

    await repo.log_agent_event(
        event_type="PAPER_ACCOUNT_RESET",
        component="portfolio",
        severity="INFO",
        message=f"Operator {user.username} reset PAPER Testing Lab to ${float(body.initial_capital):.2f} USDT.",
        payload={"initial_capital": float(body.initial_capital)},
    )
    await AuditLogger.log(
        session,
        actor=user.username,
        action="PAPER_ACCOUNT_RESET",
        resource_type="account",
        resource_id=str(account.id),
        details={"initial_capital": str(body.initial_capital)},
    )

    return {
        "status": "paper_reset",
        "trading_mode": account.trading_mode,
        "initial_capital": float(body.initial_capital),
        "available_balance": float(body.initial_capital),
        "total_equity": float(body.initial_capital),
        "closed_positions_count": len(open_positions),
    }
