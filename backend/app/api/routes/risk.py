"""Risk status & survival state endpoints."""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user, get_survival_controller, rate_limit_guard
from app.config import get_settings
from app.database.models import User
from app.database.repositories.trading_repo import TradingRepository
from app.database.session import get_db_session
from app.risk.survival_controller import SurvivalController

router = APIRouter(tags=["risk"])


@router.get("/risk/status", dependencies=[Depends(rate_limit_guard)])
async def get_risk_status(
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    survival: Annotated[SurvivalController, Depends(get_survival_controller)],
) -> dict[str, Any]:
    settings = get_settings()
    repo = TradingRepository(session)
    account = await repo.get_or_create_default_account()
    snap = await repo.get_latest_snapshot(account.id)
    decisions = await repo.get_recent_risk_decisions(limit=20)
    open_pos = await repo.get_open_positions(account.id)

    equity = snap.total_equity if snap else account.initial_capital
    dd_pct = snap.drawdown_pct if snap else Decimal("0")
    remaining_dd_budget = max(Decimal("0"), settings.max_drawdown - dd_pct)

    return {
        "survival_state": survival.state.value if survival.emergency_stopped else account.survival_state,
        "emergency_stopped": account.emergency_stopped or survival.emergency_stopped,
        "emergency_stop_reason": survival.emergency_stop_reason,
        "kelly_mode": settings.kelly_mode.value,
        "kelly_multiplier": float(settings.effective_kelly_multiplier()),
        "limits": {
            "max_risk_per_trade": float(settings.max_risk_per_trade),
            "max_daily_loss": float(settings.max_daily_loss),
            "max_drawdown": float(settings.max_drawdown),
            "max_open_positions": settings.max_open_positions,
            "max_leverage": settings.max_leverage,
        },
        "current": {
            "equity": float(equity),
            "peak_equity": float(account.peak_equity),
            "drawdown_pct": float(dd_pct),
            "remaining_drawdown_budget_pct": float(remaining_dd_budget),
            "open_positions": len(open_pos),
        },
        "recent_decisions": [
            {
                "id": str(d.id),
                "symbol": d.symbol,
                "strategy_name": d.strategy_name,
                "market_regime": d.market_regime,
                "survival_state": d.survival_state,
                "decision": d.decision,
                "win_probability": float(d.win_probability),
                "expected_value": float(d.expected_value),
                "fractional_kelly": float(d.fractional_kelly),
                "approved_notional": float(d.approved_notional),
                "rejection_reasons": d.rejection_reasons,
                "created_at": d.created_at.isoformat(),
            }
            for d in decisions
        ],
    }
