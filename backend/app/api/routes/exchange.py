"""Exchange credential configuration, read-only balance verification, and live-mode safety gating."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import (
    get_current_user,
    get_survival_controller,
    rate_limit_guard,
    require_role,
)
from app.config import TradingMode
from app.database.models import Approval, User
from app.database.repositories.trading_repo import TradingRepository
from app.database.session import get_db_session
from app.exchanges.credentials_service import ExchangeConnectionService
from app.risk.survival_controller import SurvivalController
from app.security.audit_logger import AuditLogger
from app.security.authorization import Role

router = APIRouter(tags=["exchange"])

LIVE_CONFIRMATION_PHRASE = "ENABLE LIVE TRADING"


class ExchangeCredentialsBody(BaseModel):
    exchange_id: str = Field(default="binance", min_length=2, max_length=32)
    api_key: str = Field(min_length=8, max_length=256)
    api_secret: str = Field(min_length=8, max_length=256)
    api_password: str = Field(default="", max_length=256)
    sandbox: bool = False
    enter_read_only: bool = True


class SwitchTradingModeBody(BaseModel):
    target_mode: Literal["PAPER", "TESTNET", "LIVE_READ_ONLY", "LIVE_TRADING"]
    risk_acknowledged: bool = False
    confirmation_phrase: str = ""


class SwitchMarketTypeBody(BaseModel):
    market_type: Literal["SPOT", "FUTURES"]
    leverage: int = Field(default=1, ge=1, le=5)
    futures_acknowledged: bool = False


@router.get("/exchange/status", dependencies=[Depends(rate_limit_guard)])
async def get_exchange_status(
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict[str, Any]:
    repo = TradingRepository(session)
    account = await repo.get_or_create_default_account()
    cred = await ExchangeConnectionService.get_credential(session, account.id)
    return ExchangeConnectionService.status_payload(account, cred)


@router.post("/exchange/credentials", dependencies=[Depends(rate_limit_guard)])
async def save_exchange_credentials(
    body: ExchangeCredentialsBody,
    user: Annotated[User, Depends(require_role(Role.ADMIN))],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict[str, Any]:
    repo = TradingRepository(session)
    account = await repo.get_or_create_default_account()
    try:
        cred = await ExchangeConnectionService.save_credentials(
            session,
            account=account,
            exchange_id=body.exchange_id,
            api_key=body.api_key,
            api_secret=body.api_secret,
            api_password=body.api_password,
            sandbox=body.sandbox,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    sync = await ExchangeConnectionService.verify_and_sync_balances(
        session,
        account=account,
        switch_to_read_only_if_paper=body.enter_read_only and not body.sandbox,
        reset_baseline_from_exchange=True,
    )

    await repo.log_agent_event(
        event_type="EXCHANGE_CREDENTIALS_UPDATED",
        component="exchange_security",
        severity="INFO" if sync.verified else "WARNING",
        message=(
            f"Admin {user.username} connected {cred.exchange_id} ({cred.api_key_mask}) "
            f"- verified={sync.verified}, mode={account.trading_mode}."
        ),
        payload={
            "exchange_id": cred.exchange_id,
            "api_key_mask": cred.api_key_mask,
            "sandbox": cred.sandbox,
            "verified": sync.verified,
            "trading_mode": account.trading_mode,
        },
    )
    await AuditLogger.log(
        session,
        actor=user.username,
        action="EXCHANGE_CREDENTIALS_SAVED",
        resource_type="exchange_credential",
        resource_id=str(cred.id),
        details={
            "exchange_id": cred.exchange_id,
            "api_key_mask": cred.api_key_mask,
            "sandbox": cred.sandbox,
            "verified": sync.verified,
            "trading_mode": account.trading_mode,
        },
    )

    status_info = ExchangeConnectionService.status_payload(account, cred)
    return {
        **status_info,
        "sync": {
            "verified": sync.verified,
            "quote_currency": sync.quote_currency,
            "available_quote": float(sync.available_quote),
            "total_quote": float(sync.total_quote),
            "balances": sync.balances,
            "error": sync.error,
        },
    }


@router.post("/exchange/sync-balances", dependencies=[Depends(rate_limit_guard)])
async def sync_exchange_balances(
    user: Annotated[User, Depends(require_role(Role.OPERATOR))],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict[str, Any]:
    repo = TradingRepository(session)
    account = await repo.get_or_create_default_account()
    cred = await ExchangeConnectionService.get_credential(session, account.id)
    if cred is None:
        status_info = ExchangeConnectionService.status_payload(account, None)
        if not status_info["credentials_configured"]:
            raise HTTPException(
                status_code=400,
                detail="Configure your Exchange API Key and Secret before syncing real balances.",
            )

    sync = await ExchangeConnectionService.verify_and_sync_balances(
        session,
        account=account,
        switch_to_read_only_if_paper=True,
        reset_baseline_from_exchange=account.trading_mode == TradingMode.PAPER.value,
    )
    if not sync.verified:
        await repo.log_agent_event(
            event_type="EXCHANGE_SYNC_FAILED",
            component="exchange_security",
            severity="WARNING",
            message=f"Balance sync failed for {sync.exchange_id}: {sync.error}",
        )
        raise HTTPException(status_code=400, detail=sync.error or "Failed to fetch exchange balances.")

    await repo.log_agent_event(
        event_type="EXCHANGE_BALANCES_SYNCED",
        component="portfolio",
        severity="INFO",
        message=f"Synced {sync.exchange_id} {account.market_type.capitalize()} balances ({sync.quote_currency} {sync.total_quote:.4f}) in {account.trading_mode}.",
        payload={"balances": sync.balances},
    )
    await AuditLogger.log(
        session,
        actor=user.username,
        action="EXCHANGE_BALANCES_SYNCED",
        resource_type="account",
        resource_id=str(account.id),
        details={"exchange_id": sync.exchange_id, "total_quote": str(sync.total_quote)},
    )

    return {
        **ExchangeConnectionService.status_payload(account, cred),
        "sync": {
            "verified": sync.verified,
            "quote_currency": sync.quote_currency,
            "available_quote": float(sync.available_quote),
            "total_quote": float(sync.total_quote),
            "balances": sync.balances,
        },
    }


@router.post("/exchange/mode", dependencies=[Depends(rate_limit_guard)])
async def switch_trading_mode(
    body: SwitchTradingModeBody,
    user: Annotated[User, Depends(require_role(Role.ADMIN))],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    survival: Annotated[SurvivalController, Depends(get_survival_controller)],
) -> dict[str, Any]:
    repo = TradingRepository(session)
    account = await repo.get_or_create_default_account()
    cred = await ExchangeConnectionService.get_credential(session, account.id)
    target = TradingMode(body.target_mode)

    if target == TradingMode.LIVE_TRADING:
        if account.emergency_stopped or survival.emergency_stopped:
            raise HTTPException(
                status_code=400,
                detail="Cannot arm LIVE_TRADING while emergency stop is active.",
            )
        if cred is None or not cred.verified:
            raise HTTPException(
                status_code=400,
                detail="Verify your Exchange API credentials and sync balances in LIVE_READ_ONLY first.",
            )
        if cred.sandbox:
            raise HTTPException(
                status_code=400,
                detail="Sandbox/testnet credentials cannot enter LIVE_TRADING. Disable sandbox for real Spot trading.",
            )
        if not body.risk_acknowledged:
            raise HTTPException(
                status_code=400,
                detail="You must acknowledge real-capital trading risk before enabling LIVE_TRADING.",
            )
        if body.confirmation_phrase.strip().upper() != LIVE_CONFIRMATION_PHRASE:
            raise HTTPException(
                status_code=400,
                detail=f'Type "{LIVE_CONFIRMATION_PHRASE}" exactly to authorize LIVE_TRADING.',
            )

        approval = Approval(
            action_type="LIVE_TRADING",
            status="APPROVED",
            requested_by=user.username,
            reviewed_by=user.username,
            summary=f"Admin {user.username} armed LIVE_TRADING on {cred.exchange_id} ({cred.api_key_mask}).",
            payload={"exchange_id": cred.exchange_id, "api_key_mask": cred.api_key_mask},
            review_note="Explicit dashboard confirmation gate",
            reviewed_at=datetime.now(timezone.utc),
        )
        session.add(approval)
        account.trading_mode = TradingMode.LIVE_TRADING.value
        account.live_trading_authorized = True
        account.is_trading_active = False

        await repo.log_agent_event(
            event_type="LIVE_TRADING_AUTHORIZED",
            component="exchange_security",
            severity="WARNING",
            message=f"LIVE_TRADING authorized by {user.username} on {cred.exchange_id}. Loop paused until started.",
        )
    elif target == TradingMode.LIVE_READ_ONLY:
        if cred is None or not cred.verified:
            raise HTTPException(
                status_code=400,
                detail="Verify Exchange API credentials first before entering LIVE_READ_ONLY.",
            )
        account.trading_mode = TradingMode.LIVE_READ_ONLY.value
        account.live_trading_authorized = False
        account.is_trading_active = False
        await ExchangeConnectionService.verify_and_sync_balances(
            session,
            account=account,
            switch_to_read_only_if_paper=False,
            reset_baseline_from_exchange=False,
        )
    elif target == TradingMode.TESTNET:
        if cred is None or not cred.sandbox:
            raise HTTPException(
                status_code=400,
                detail="TESTNET mode requires verified sandbox/testnet API credentials.",
            )
        account.trading_mode = TradingMode.TESTNET.value
        account.live_trading_authorized = False
        account.is_trading_active = False
    else:
        account.trading_mode = TradingMode.PAPER.value
        account.live_trading_authorized = False
        account.is_trading_active = False

    await session.flush()
    await AuditLogger.log(
        session,
        actor=user.username,
        action="TRADING_MODE_CHANGED",
        resource_type="account",
        resource_id=str(account.id),
        details={
            "trading_mode": account.trading_mode,
            "live_trading_authorized": account.live_trading_authorized,
        },
    )
    return ExchangeConnectionService.status_payload(account, cred)


@router.post("/exchange/market-type", dependencies=[Depends(rate_limit_guard)])
async def switch_market_type(
    body: SwitchMarketTypeBody,
    user: Annotated[User, Depends(require_role(Role.ADMIN))],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict[str, Any]:
    repo = TradingRepository(session)
    account = await repo.get_or_create_default_account()

    if body.market_type != "FUTURES":
        raise HTTPException(
            status_code=400,
            detail="Spot trading is disabled. Only USD-M Futures (1x Isolated) is supported.",
        )
    if body.leverage != 1:
        raise HTTPException(
            status_code=400,
            detail="Leverage is locked to 1x Isolated under Survival-First risk policy.",
        )
    if not body.futures_acknowledged:
        raise HTTPException(
            status_code=400,
            detail="Explicit futures risk acknowledgment (futures_acknowledged=true) is required.",
        )

    open_positions = await repo.get_open_positions(account.id)
    if open_positions and account.market_type != "FUTURES":
        raise HTTPException(
            status_code=400,
            detail="Close all legacy open positions before switching to FUTURES.",
        )

    # Futures-only policy at 1x leverage
    account.market_type = "FUTURES"
    account.leverage = 1
    account.is_trading_active = False
    await session.flush()

    cred = await ExchangeConnectionService.get_credential(session, account.id)
    sync_info: dict[str, Any] | None = None
    if cred is not None and account.trading_mode in {
        TradingMode.LIVE_READ_ONLY.value,
        TradingMode.LIVE_TRADING.value,
        TradingMode.TESTNET.value,
    }:
        sync = await ExchangeConnectionService.verify_and_sync_balances(
            session,
            account=account,
            switch_to_read_only_if_paper=False,
            reset_baseline_from_exchange=True,
        )
        sync_info = {
            "verified": sync.verified,
            "quote_currency": sync.quote_currency,
            "available_quote": float(sync.available_quote),
            "total_quote": float(sync.total_quote),
            "balances": sync.balances,
            "error": sync.error,
        }

    await repo.log_agent_event(
        event_type="MARKET_TYPE_CHANGED",
        component="exchange_security",
        severity="WARNING" if body.market_type == "FUTURES" else "INFO",
        message=f"Admin {user.username} set market_type={account.market_type}, leverage={account.leverage}x (ISOLATED).",
    )
    await AuditLogger.log(
        session,
        actor=user.username,
        action="MARKET_TYPE_CHANGED",
        resource_type="account",
        resource_id=str(account.id),
        details={"market_type": account.market_type, "leverage": account.leverage},
    )

    payload = ExchangeConnectionService.status_payload(account, cred)
    if sync_info is not None:
        payload["sync"] = sync_info
    return payload
