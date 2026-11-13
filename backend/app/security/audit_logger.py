"""Append-only tamper-evident audit logger with SHA-256 hash chaining."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import AuditLog
from app.security.secrets_manager import SecretsManager

GENESIS_HASH = "0" * 64


def compute_audit_hash(
    *,
    actor: str,
    action: str,
    resource_type: str,
    resource_id: str | None,
    details: dict[str, Any],
    previous_hash: str,
    created_at: datetime,
) -> str:
    ts_utc = (
        created_at.replace(tzinfo=timezone.utc)
        if created_at.tzinfo is None
        else created_at.astimezone(timezone.utc)
    )
    canonical = json.dumps(
        {
            "actor": actor,
            "action": action,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "details": details,
            "previous_hash": previous_hash,
            "created_at": ts_utc.isoformat(),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class AuditLogger:
    """Writes hash-chained audit records and verifies chain integrity."""

    @staticmethod
    async def log(
        session: AsyncSession,
        *,
        actor: str,
        action: str,
        resource_type: str,
        resource_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> AuditLog:
        safe_details = SecretsManager.redact_dict(details or {})
        stmt = select(AuditLog).order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).limit(1)
        result = await session.execute(stmt)
        prev_row = result.scalar_one_or_none()
        prev_hash = prev_row.record_hash if prev_row else GENESIS_HASH

        created_at = datetime.now(timezone.utc)
        if prev_row and prev_row.created_at:
            prev_ts = (
                prev_row.created_at.replace(tzinfo=timezone.utc)
                if prev_row.created_at.tzinfo is None
                else prev_row.created_at.astimezone(timezone.utc)
            )
            if created_at <= prev_ts:
                created_at = prev_ts + timedelta(microseconds=1)
        rec_hash = compute_audit_hash(
            actor=actor,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            details=safe_details,
            previous_hash=prev_hash,
            created_at=created_at,
        )
        entry = AuditLog(
            actor=actor,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            details=safe_details,
            previous_hash=prev_hash,
            record_hash=rec_hash,
            created_at=created_at,
        )
        session.add(entry)
        await session.flush()
        return entry

    @staticmethod
    async def verify_chain(session: AsyncSession) -> tuple[bool, int]:
        stmt = select(AuditLog).order_by(AuditLog.created_at.asc(), AuditLog.id.asc())
        result = await session.execute(stmt)
        rows = list(result.scalars().all())
        expected_prev = GENESIS_HASH
        for row in rows:
            if row.previous_hash != expected_prev:
                return False, len(rows)
            expected_hash = compute_audit_hash(
                actor=row.actor,
                action=row.action,
                resource_type=row.resource_type,
                resource_id=row.resource_id,
                details=row.details,
                previous_hash=row.previous_hash,
                created_at=row.created_at,
            )
            if row.record_hash != expected_hash:
                return False, len(rows)
            expected_prev = row.record_hash
        return True, len(rows)
