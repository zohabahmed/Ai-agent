"""Dashboard & Auth summary endpoints."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import (
    get_current_user,
    get_market_cache,
    get_strategy_registry,
    get_survival_controller,
    rate_limit_guard,
)
from app.config import get_settings
from app.database.models import User
from app.database.repositories.trading_repo import TradingRepository
from app.database.session import get_db_manager, get_db_session
from app.market.market_cache import MarketCache
from app.portfolio.balance_manager import BalanceManager
from app.portfolio.equity_calculator import EquityCalculator
from app.portfolio.profit_manager import ProfitManager
from app.risk.survival_controller import SurvivalController
from app.security.audit_logger import AuditLogger
from app.security.authentication import create_access_token, verify_password
from app.strategies.strategy_registry import StrategyRegistry

router = APIRouter(tags=["dashboard"])


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    username: str
    role: str


@router.get("/health")
async def health_check(
    cache: Annotated[MarketCache, Depends(get_market_cache)],
) -> dict[str, Any]:
    db_ok = await get_db_manager().health_check()
    cache_health = await cache.health_check()
    settings = get_settings()
    return {
        "status": "healthy" if db_ok else "degraded",
        "database": "connected" if db_ok else "disconnected",
        "cache": cache_health,
        "trading_mode": settings.trading_mode.value,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.post("/auth/token", response_model=TokenResponse, dependencies=[Depends(rate_limit_guard)])
async def login_for_access_token(
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> TokenResponse:
    repo = TradingRepository(session)
    user = await repo.get_user_by_username(form_data.username)
    if user is None or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = create_access_token(subject=user.username, role=user.role)
    await AuditLogger.log(
        session,
        actor=user.username,
        action="USER_LOGIN",
        resource_type="user",
        resource_id=str(user.id),
    )
    return TokenResponse(access_token=token, username=user.username, role=user.role)


@router.get("/auth/me")
async def read_current_user(user: Annotated[User, Depends(get_current_user)]) -> dict[str, Any]:
    return {"id": str(user.id), "username": user.username, "role": user.role, "is_active": user.is_active}


@router.get("/dashboard", dependencies=[Depends(rate_limit_guard)])
async def get_dashboard(
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    survival: Annotated[SurvivalController, Depends(get_survival_controller)],
    registry: Annotated[StrategyRegistry, Depends(get_strategy_registry)],
    cache: Annotated[MarketCache, Depends(get_market_cache)],
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
    avail = await bal_mgr.get_quote_available(account.id, account.quote_currency)
    locked = await bal_mgr.get_quote_locked(account.id, account.quote_currency)
    open_pos = await repo.get_open_positions(account.id)
    trades = await repo.get_recent_trades(account.id, limit=100)
    latest_snap = await repo.get_latest_snapshot(account.id)

    pos_tuples = [
        (p.quantity, p.entry_price, p.current_price, p.entry_fee, p.side, p.market_type, p.margin_used)
        for p in open_pos
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

    tickers: dict[str, Any] = {}
    for sym in settings.symbols:
        cached = await cache.get_json(f"market:ticker:{sym}")
        if cached:
            tickers[sym] = cached

    strategies_db = await repo.list_strategies()
    approvals_pending = await repo.list_approvals(status="PENDING")
    events = await repo.get_recent_events(limit=25)
    risk_decisions = await repo.get_recent_risk_decisions(limit=15)

    return {
        "account": {
            "id": str(account.id),
            "name": account.name,
            "exchange": account.exchange,
            "market_type": account.market_type,
            "leverage": account.leverage,
            "trading_mode": account.trading_mode,
            "autonomy_mode": account.autonomy_mode,
            "is_simulated": account.trading_mode in {"PAPER", "BACKTEST"},
            "is_trading_active": account.is_trading_active,
            "emergency_stopped": account.emergency_stopped or survival.emergency_stopped,
            "live_trading_authorized": account.live_trading_authorized,
        },
        "overview": {
            "initial_capital": float(eq.initial_capital),
            "current_equity": float(eq.total_equity),
            "available_balance": float(eq.available_balance),
            "locked_balance": float(eq.locked_balance),
            "open_position_value": float(eq.open_position_value),
            "realized_pnl": float(eq.realized_pnl),
            "unrealized_pnl": float(eq.unrealized_pnl),
            "total_trading_costs": float(eq.total_fees),
            "net_return_pct": float(eq.net_return_pct),
            "current_drawdown_pct": float(drawdown_pct),
        },
        "survival": {
            "state": survival.state.value if survival.emergency_stopped else account.survival_state,
            "kelly_mode": settings.kelly_mode.value,
            "kelly_multiplier": float(settings.effective_kelly_multiplier()),
            "max_risk_per_trade": float(settings.max_risk_per_trade),
            "max_daily_loss": float(settings.max_daily_loss),
            "max_drawdown": float(settings.max_drawdown),
            "max_open_positions": settings.max_open_positions,
            "open_positions_count": len(open_pos),
            "emergency_stopped": account.emergency_stopped or survival.emergency_stopped,
        },
        "market_tickers": tickers,
        "strategies": [
            {
                "name": s.name,
                "display_name": s.display_name,
                "category": s.category,
                "status": s.status,
                "enabled_in_registry": registry.is_enabled(s.name),
                "preferred_regimes": s.preferred_regimes,
            }
            for s in strategies_db
        ],
        "withdrawals": {
            "realized_withdrawable_profit": float(profit.withdrawable_profit),
            "reserve_capital": float(profit.reserve_capital_required),
            "withdrawal_threshold": float(settings.withdrawal_threshold_usd),
        },
        "pending_approvals_count": len(approvals_pending),
        "recent_risk_decisions": [
            {
                "id": str(rd.id),
                "symbol": rd.symbol,
                "strategy": rd.strategy_name,
                "regime": rd.market_regime,
                "decision": rd.decision,
                "kelly_fraction": float(rd.fractional_kelly),
                "expected_value": float(rd.expected_value),
                "rejection_reasons": rd.rejection_reasons,
                "created_at": rd.created_at.isoformat(),
            }
            for rd in risk_decisions
        ],
        "recent_events": [
            {
                "id": str(ev.id),
                "event_type": ev.event_type,
                "severity": ev.severity,
                "component": ev.component,
                "message": ev.message,
                "created_at": ev.created_at.isoformat(),
            }
            for ev in events
        ],
        "system": {
            "database": "connected",
            "cache": await cache.health_check(),
            "market_worker": await cache.get_json("worker:market:heartbeat"),
            "trading_worker": await cache.get_json("worker:trading:heartbeat"),
            "monitoring_worker": await cache.get_json("worker:monitoring:heartbeat"),
        },
    }


@router.get("/agent/events", dependencies=[Depends(rate_limit_guard)])
async def get_agent_events(
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    limit: int = 100,
) -> dict[str, Any]:
    repo = TradingRepository(session)
    events = await repo.get_recent_events(limit=min(limit, 500))
    return {
        "events": [
            {
                "id": str(ev.id),
                "event_type": ev.event_type,
                "severity": ev.severity,
                "component": ev.component,
                "message": ev.message,
                "payload": ev.payload,
                "created_at": ev.created_at.isoformat(),
            }
            for ev in events
        ]
    }
