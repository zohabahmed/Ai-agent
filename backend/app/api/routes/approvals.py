"""Human-in-the-loop approval endpoints."""

from __future__ import annotations

from typing import Annotated, Any
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user, rate_limit_guard, require_role
from app.database.models import User
from app.database.repositories.trading_repo import TradingRepository
from app.database.session import get_db_session
from app.security.authorization import Role
from app.withdrawals.approval_service import ApprovalService

router = APIRouter(tags=["approvals"])


class ApprovalReviewBody(BaseModel):
    note: str | None = None


@router.get("/approvals", dependencies=[Depends(rate_limit_guard)])
async def list_approvals(
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    status: str | None = None,
) -> dict[str, Any]:
    repo = TradingRepository(session)
    rows = await repo.list_approvals(status=status)
    return {
        "approvals": [
            {
                "id": str(a.id),
                "action_type": a.action_type,
                "status": a.status,
                "requested_by": a.requested_by,
                "reviewed_by": a.reviewed_by,
                "summary": a.summary,
                "payload": a.payload,
                "review_note": a.review_note,
                "created_at": a.created_at.isoformat(),
                "reviewed_at": a.reviewed_at.isoformat() if a.reviewed_at else None,
            }
            for a in rows
        ]
    }


@router.post("/approvals/{approval_id}/approve", dependencies=[Depends(rate_limit_guard)])
async def approve_request(
    approval_id: uuid.UUID,
    user: Annotated[User, Depends(require_role(Role.ADMIN))],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    body: ApprovalReviewBody = ApprovalReviewBody(),
) -> dict[str, Any]:
    try:
        app_row = await ApprovalService.approve(
            session, approval_id=approval_id, reviewer=user.username, note=body.note
        )
    except (ValueError, PermissionError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "id": str(app_row.id),
        "status": app_row.status,
        "reviewed_by": app_row.reviewed_by,
        "reviewed_at": app_row.reviewed_at.isoformat() if app_row.reviewed_at else None,
    }


@router.post("/approvals/{approval_id}/reject", dependencies=[Depends(rate_limit_guard)])
async def reject_request(
    approval_id: uuid.UUID,
    user: Annotated[User, Depends(require_role(Role.ADMIN))],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    body: ApprovalReviewBody = ApprovalReviewBody(),
) -> dict[str, Any]:
    try:
        app_row = await ApprovalService.reject(
            session, approval_id=approval_id, reviewer=user.username, note=body.note
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "id": str(app_row.id),
        "status": app_row.status,
        "reviewed_by": app_row.reviewed_by,
        "reviewed_at": app_row.reviewed_at.isoformat() if app_row.reviewed_at else None,
    }
