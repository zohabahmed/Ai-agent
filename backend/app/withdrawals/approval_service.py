"""Human-in-the-loop Approval Service for sensitive financial & governance operations."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Approval
from app.security.audit_logger import AuditLogger

SENSITIVE_ACTIONS = {
    "WITHDRAWAL",
    "LIVE_TRADING",
    "RISK_LIMIT_CHANGE",
    "NEW_STRATEGY",
    "DESTINATION_ADDRESS",
    "EXCHANGE_CREDENTIALS",
}


class ApprovalService:
    """Manages explicit owner approvals. AI cannot self-approve sensitive actions."""

    @staticmethod
    async def request_approval(
        session: AsyncSession,
        *,
        action_type: str,
        summary: str,
        payload: dict[str, Any],
        requested_by: str = "agent",
    ) -> Approval:
        approval = Approval(
            action_type=action_type.upper(),
            status="PENDING",
            requested_by=requested_by,
            summary=summary,
            payload=payload,
        )
        session.add(approval)
        await session.flush()
        await AuditLogger.log(
            session,
            actor=requested_by,
            action="APPROVAL_REQUESTED",
            resource_type="approval",
            resource_id=str(approval.id),
            details={"action_type": approval.action_type, "summary": summary},
        )
        return approval

    @staticmethod
    async def approve(
        session: AsyncSession,
        *,
        approval_id: uuid.UUID,
        reviewer: str,
        note: str | None = None,
    ) -> Approval:
        if reviewer in {"agent", "ai", "system", "autonomous_agent"}:
            raise PermissionError("AI or autonomous system cannot approve its own sensitive requests.")

        stmt = select(Approval).where(Approval.id == approval_id)
        res = await session.execute(stmt)
        approval = res.scalar_one_or_none()
        if approval is None:
            raise ValueError("Approval request not found.")
        if approval.status != "PENDING":
            raise ValueError(f"Approval request is already {approval.status}.")

        approval.status = "APPROVED"
        approval.reviewed_by = reviewer
        approval.review_note = note
        approval.reviewed_at = datetime.now(timezone.utc)
        await session.flush()

        await AuditLogger.log(
            session,
            actor=reviewer,
            action="APPROVAL_GRANTED",
            resource_type="approval",
            resource_id=str(approval.id),
            details={"action_type": approval.action_type, "note": note},
        )
        return approval

    @staticmethod
    async def reject(
        session: AsyncSession,
        *,
        approval_id: uuid.UUID,
        reviewer: str,
        note: str | None = None,
    ) -> Approval:
        stmt = select(Approval).where(Approval.id == approval_id)
        res = await session.execute(stmt)
        approval = res.scalar_one_or_none()
        if approval is None:
            raise ValueError("Approval request not found.")
        if approval.status != "PENDING":
            raise ValueError(f"Approval request is already {approval.status}.")

        approval.status = "REJECTED"
        approval.reviewed_by = reviewer
        approval.review_note = note
        approval.reviewed_at = datetime.now(timezone.utc)
        await session.flush()

        await AuditLogger.log(
            session,
            actor=reviewer,
            action="APPROVAL_REJECTED",
            resource_type="approval",
            resource_id=str(approval.id),
            details={"action_type": approval.action_type, "note": note},
        )
        return approval
