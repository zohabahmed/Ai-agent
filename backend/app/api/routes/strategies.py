"""Strategies, performance metrics, versioning, and rollback endpoints."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adaptive.strategy_versioning import StrategyVersioningService
from app.api.dependencies import (
    get_current_user,
    get_strategy_registry,
    rate_limit_guard,
    require_role,
)
from app.database.models import Strategy, User
from app.database.repositories.trading_repo import TradingRepository
from app.database.session import get_db_session
from app.security.authorization import Role
from app.strategies.strategy_registry import StrategyRegistry

router = APIRouter(tags=["strategies"])


@router.get("/strategies", dependencies=[Depends(rate_limit_guard)])
async def list_strategies(
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    registry: Annotated[StrategyRegistry, Depends(get_strategy_registry)],
) -> dict[str, Any]:
    repo = TradingRepository(session)
    db_strats = await repo.list_strategies()
    versions = await repo.get_strategy_versions(limit=50)
    return {
        "strategies": [
            {
                "id": str(s.id),
                "name": s.name,
                "display_name": s.display_name,
                "category": s.category,
                "status": s.status,
                "is_approved_live": s.is_approved_live,
                "enabled_in_registry": registry.is_enabled(s.name),
                "preferred_regimes": s.preferred_regimes,
                "parameters": s.parameters,
                "updated_at": s.updated_at.isoformat(),
            }
            for s in db_strats
        ],
        "versions": [
            {
                "id": str(v.id),
                "strategy_id": str(v.strategy_id),
                "version_number": v.version_number,
                "status": v.status,
                "change_reason": v.change_reason,
                "validation_summary": v.validation_summary,
                "created_by": v.created_by,
                "created_at": v.created_at.isoformat(),
            }
            for v in versions
        ],
    }


@router.get("/strategies/metrics", dependencies=[Depends(rate_limit_guard)])
async def get_strategy_metrics(
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict[str, Any]:
    repo = TradingRepository(session)
    metrics = await repo.get_strategy_metrics(limit=100)
    return {
        "metrics": [
            {
                "id": str(m.id),
                "strategy_id": str(m.strategy_id),
                "window_name": m.window_name,
                "regime": m.regime,
                "trade_count": m.trade_count,
                "win_rate": float(m.win_rate),
                "avg_win": float(m.avg_win),
                "avg_loss": float(m.avg_loss),
                "profit_factor": float(m.profit_factor),
                "expected_value": float(m.expected_value),
                "sharpe_ratio": float(m.sharpe_ratio),
                "sortino_ratio": float(m.sortino_ratio),
                "max_drawdown": float(m.max_drawdown),
                "consecutive_losses": m.consecutive_losses,
                "drift_detected": m.drift_detected,
                "drift_score": float(m.drift_score),
                "recorded_at": m.recorded_at.isoformat(),
            }
            for m in metrics
        ]
    }


@router.post("/strategies/{name}/rollback", dependencies=[Depends(rate_limit_guard)])
async def rollback_strategy(
    name: str,
    user: Annotated[User, Depends(require_role(Role.OPERATOR))],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    registry: Annotated[StrategyRegistry, Depends(get_strategy_registry)],
) -> dict[str, Any]:
    res = await session.execute(select(Strategy).where(Strategy.name == name))
    strat = res.scalar_one_or_none()
    if strat is None:
        raise HTTPException(status_code=404, detail=f"Strategy {name} not found.")
    try:
        prev_ver = await StrategyVersioningService.rollback_to_previous(
            session, strategy=strat, actor=user.username
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    registry.enable(name)
    registry.update_params(name, prev_ver.parameters)
    return {
        "strategy": name,
        "rolled_back_to_version": prev_ver.version_number,
        "status": strat.status,
    }
