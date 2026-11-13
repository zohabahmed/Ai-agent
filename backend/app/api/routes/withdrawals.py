"""Withdrawal & destination address endpoints."""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Any
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user, rate_limit_guard, require_role
from app.config import get_settings
from app.database.models import Position, User
from app.database.repositories.trading_repo import TradingRepository
from app.database.session import get_db_session
from app.portfolio.balance_manager import BalanceManager
from app.portfolio.profit_manager import ProfitManager
from app.security.authorization import Role
from app.withdrawals.address_manager import SUPPORTED_NETWORKS, AddressManager
from app.withdrawals.withdrawal_manager import WithdrawalManager

router = APIRouter(tags=["withdrawals"])


class WithdrawalRequestBody(BaseModel):
    amount: Decimal = Field(gt=Decimal("0"))
    destination_address: str
    network: str
    asset: str = "USDT"


class RegisterAddressBody(BaseModel):
    label: str
    asset: str = "USDT"
    network: str
    address: str
    destination_type: str = "WALLET"


@router.get("/withdrawals", dependencies=[Depends(rate_limit_guard)])
async def get_withdrawals(
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict[str, Any]:
    settings = get_settings()
    repo = TradingRepository(session)
    account = await repo.get_or_create_default_account()
    bal_mgr = BalanceManager(session)
    avail = await bal_mgr.get_quote_available(account.id, account.quote_currency)

    realized_stmt = select(func.coalesce(func.sum(Position.realized_pnl), 0)).where(
        Position.account_id == account.id,
        Position.status == "CLOSED",
    )
    realized_res = await session.execute(realized_stmt)
    realized_pnl = Decimal(str(realized_res.scalar_one() or 0))

    profit = ProfitManager.calculate_withdrawable(
        realized_net_profit=realized_pnl,
        unrealized_pnl=Decimal("0"),
        available_cash=avail,
        min_reserve_capital=settings.min_reserve_capital_usd,
        profit_allocation_pct=settings.profit_allocation_pct,
    )
    requests = await repo.list_withdrawals(account.id)
    addresses = await repo.list_destination_addresses()

    return {
        "summary": {
            "realized_net_profit": float(profit.realized_net_profit),
            "withdrawable_profit": float(profit.withdrawable_profit),
            "reserve_capital": float(profit.reserve_capital_required),
            "withdrawal_threshold": float(settings.withdrawal_threshold_usd),
            "supported_networks": SUPPORTED_NETWORKS,
        },
        "addresses": [
            {
                "id": str(a.id),
                "label": a.label,
                "asset": a.asset,
                "network": a.network,
                "address": a.address,
                "destination_type": a.destination_type,
                "is_verified": a.is_verified,
            }
            for a in addresses
        ],
        "requests": [
            {
                "id": str(w.id),
                "asset": w.asset,
                "network": w.network,
                "destination_address": w.destination_address,
                "requested_amount": float(w.requested_amount),
                "network_fee": float(w.network_fee),
                "net_amount": float(w.net_amount),
                "status": w.status,
                "approval_id": str(w.approval_id) if w.approval_id else None,
                "tx_id": w.tx_id,
                "created_at": w.created_at.isoformat(),
            }
            for w in requests
        ],
    }


@router.post("/withdrawals/request", dependencies=[Depends(rate_limit_guard)])
async def request_withdrawal(
    body: WithdrawalRequestBody,
    user: Annotated[User, Depends(require_role(Role.OPERATOR))],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict[str, Any]:
    repo = TradingRepository(session)
    account = await repo.get_or_create_default_account()
    mgr = WithdrawalManager()
    try:
        wr = await mgr.create_withdrawal_request(
            session,
            account=account,
            amount=body.amount,
            destination_address=body.destination_address,
            network=body.network,
            asset=body.asset,
            requested_by=user.username,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "id": str(wr.id),
        "approval_id": str(wr.approval_id),
        "status": wr.status,
        "net_amount": float(wr.net_amount),
    }


@router.post("/withdrawals/addresses", dependencies=[Depends(rate_limit_guard)])
async def register_destination_address(
    body: RegisterAddressBody,
    user: Annotated[User, Depends(require_role(Role.ADMIN))],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict[str, Any]:
    try:
        dest = await AddressManager.register_address(
            session,
            label=body.label,
            asset=body.asset,
            network=body.network,
            address=body.address,
            destination_type=body.destination_type,
            verified=True,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "id": str(dest.id),
        "label": dest.label,
        "network": dest.network,
        "address": dest.address,
        "is_verified": dest.is_verified,
    }


@router.post("/withdrawals/{withdrawal_id}/execute", dependencies=[Depends(rate_limit_guard)])
async def execute_withdrawal(
    withdrawal_id: uuid.UUID,
    user: Annotated[User, Depends(require_role(Role.ADMIN))],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict[str, Any]:
    mgr = WithdrawalManager()
    try:
        wr = await mgr.execute_approved_withdrawal(
            session, withdrawal_id=withdrawal_id, executor=user.username
        )
    except (ValueError, PermissionError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"id": str(wr.id), "status": wr.status, "tx_id": wr.tx_id}
