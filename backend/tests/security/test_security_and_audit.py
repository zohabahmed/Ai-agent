"""Security tests: secret redaction, encryption, JWT validation, RBAC, and tamper-evident audit chain."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.database.models import User
from app.main import bootstrap_initial_state, create_app
from app.security.audit_logger import AuditLogger
from app.security.authentication import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)
from app.security.secrets_manager import SecretsManager


def test_secrets_manager_encryption_and_redaction():
    sm = SecretsManager()
    secret_val = "binance-live-secret-xyz-999"
    enc = sm.encrypt(secret_val)
    assert enc != secret_val
    assert sm.decrypt(enc) == secret_val
    assert sm.mask_secret(secret_val) == "bi***99"

    redacted = sm.redact_dict(
        {
            "exchange": "binance",
            "api_key": "MYKEY",
            "nested": {"api_secret": "MYSECRET", "symbol": "BTC/USDT"},
        }
    )
    assert redacted["api_key"] == "[REDACTED]"
    assert redacted["nested"]["api_secret"] == "[REDACTED]"
    assert redacted["nested"]["symbol"] == "BTC/USDT"


def test_jwt_and_password_hashing():
    pw = "SuperStrongPass123!"
    hashed = hash_password(pw)
    assert verify_password(pw, hashed) is True
    assert verify_password("wrong", hashed) is False

    token = create_access_token(subject="alice", role="operator")
    decoded = decode_access_token(token)
    assert decoded is not None
    assert decoded["sub"] == "alice"
    assert decode_access_token(token + "tamper") is None


@pytest.mark.asyncio
async def test_rbac_viewer_cannot_stop_trading_or_approve(db_manager):
    await bootstrap_initial_state()
    async with db_manager.session() as session:
        session.add(
            User(
                username="readonly_viewer",
                hashed_password=hash_password("viewerpass"),
                role="viewer",
                is_active=True,
            )
        )

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        tok = await client.post(
            "/api/auth/token", data={"username": "readonly_viewer", "password": "viewerpass"}
        )
        assert tok.status_code == 200
        headers = {"Authorization": f"Bearer {tok.json()['access_token']}"}

        # Viewer can read dashboard
        d = await client.get("/api/dashboard", headers=headers)
        assert d.status_code == 200

        # Viewer CANNOT trigger trading start or emergency stop
        s = await client.post("/api/trading/start", headers=headers)
        assert s.status_code == 403
        e = await client.post("/api/trading/emergency-stop", headers=headers)
        assert e.status_code == 403


@pytest.mark.asyncio
async def test_audit_log_hash_chain_detects_tampering(db_manager):
    async with db_manager.session() as session:
        e1 = await AuditLogger.log(
            session, actor="admin", action="START", resource_type="account", details={"api_key": "secret"}
        )
        assert e1.details["api_key"] == "[REDACTED]"
        await AuditLogger.log(
            session, actor="admin", action="STOP", resource_type="account", details={"reason": "test"}
        )
        ok, count = await AuditLogger.verify_chain(session)
        assert ok is True
        assert count == 2

        # Tamper with record 1 details -> verification MUST fail
        e1.details = {"tampered": True}
        await session.flush()
        ok_after, _ = await AuditLogger.verify_chain(session)
        assert ok_after is False
