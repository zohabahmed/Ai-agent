"""Strategy Version Control with atomic activation and instant rollback."""

from __future__ import annotations

from typing import Any
import uuid

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Strategy, StrategyVersion
from app.security.audit_logger import AuditLogger


class StrategyVersioningService:
    """Maintains immutable version records of every strategy parameter/status change."""

    @staticmethod
    async def record_version(
        session: AsyncSession,
        *,
        strategy: Strategy,
        parameters: dict[str, Any],
        change_reason: str,
        validation_summary: dict[str, Any],
        status: str = "ACTIVE",
        created_by: str = "adaptive_engine",
    ) -> StrategyVersion:
        max_ver_stmt = select(func.coalesce(func.max(StrategyVersion.version_number), 0)).where(
            StrategyVersion.strategy_id == strategy.id
        )
        res = await session.execute(max_ver_stmt)
        next_ver = int(res.scalar_one() or 0) + 1

        ver = StrategyVersion(
            strategy_id=strategy.id,
            version_number=next_ver,
            parameters=parameters,
            change_reason=change_reason,
            validation_summary=validation_summary,
            status=status,
            created_by=created_by,
        )
        session.add(ver)
        await session.flush()

        if status == "ACTIVE":
            strategy.active_version_id = ver.id
            strategy.parameters = parameters
            await session.flush()

        await AuditLogger.log(
            session,
            actor=created_by,
            action="STRATEGY_VERSION_CREATED",
            resource_type="strategy_version",
            resource_id=str(ver.id),
            details={
                "strategy": strategy.name,
                "version": next_ver,
                "status": status,
                "reason": change_reason,
            },
        )
        return ver

    @staticmethod
    async def rollback_to_previous(
        session: AsyncSession,
        *,
        strategy: Strategy,
        actor: str = "operator",
        reason: str = "Rollback requested",
    ) -> StrategyVersion:
        stmt = (
            select(StrategyVersion)
            .where(StrategyVersion.strategy_id == strategy.id)
            .order_by(desc(StrategyVersion.version_number))
            .limit(2)
        )
        res = await session.execute(stmt)
        versions = list(res.scalars().all())
        if len(versions) < 2:
            raise ValueError(f"No previous version available to roll back for {strategy.name}.")

        current_ver, prev_ver = versions[0], versions[1]
        current_ver.status = "ROLLED_BACK"
        prev_ver.status = "ACTIVE"
        strategy.active_version_id = prev_ver.id
        strategy.parameters = prev_ver.parameters
        strategy.status = "ACTIVE"
        await session.flush()

        await AuditLogger.log(
            session,
            actor=actor,
            action="STRATEGY_ROLLBACK",
            resource_type="strategy",
            resource_id=str(strategy.id),
            details={
                "strategy": strategy.name,
                "from_version": current_ver.version_number,
                "to_version": prev_ver.version_number,
                "reason": reason,
            },
        )
        return prev_ver
