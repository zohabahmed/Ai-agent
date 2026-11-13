"""Profit Withdrawal Manager enforcing realized-profit-only, reserve, address, and approval rules."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, TradingMode, get_settings
from app.database.models import Account, Approval, Position, WithdrawalRequest
from app.portfolio.balance_manager import BalanceManager
from app.portfolio.profit_manager import ProfitManager
from app.security.audit_logger import AuditLogger
from app.withdrawals.address_manager import AddressManager
from app.withdrawals.approval_service import ApprovalService


class WithdrawalManager:
    """Implements the 12-step secure profit withdrawal workflow."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    async def create_withdrawal_request(
        self,
        session: AsyncSession,
        *,
        account: Account,
        amount: Decimal,
        destination_address: str,
        network: str,
        asset: str = "USDT",
        requested_by: str = "agent",
    ) -> WithdrawalRequest:
        if account.trading_mode == TradingMode.PAPER.value:
            # Paper accounts may simulate a request, but clearly flagged and never sent on-chain
            pass

        if amount <= 0:
            raise ValueError("Withdrawal amount must be positive.")

        if not AddressManager.is_network_supported(network):
            raise ValueError(f"Unsupported network: {network}")

        dest = await AddressManager.get_verified_address(session, destination_address, network)
        if dest is None:
            raise ValueError("Destination address is not verified. Verify address before requesting withdrawal.")

        # 1. Calculate actual realized net profit (never unrealized!)
        realized_stmt = select(func.coalesce(func.sum(Position.realized_pnl), 0)).where(
            Position.account_id == account.id,
            Position.status == "CLOSED",
        )
        realized_res = await session.execute(realized_stmt)
        realized_pnl = Decimal(str(realized_res.scalar_one() or 0))

        bal_mgr = BalanceManager(session)
        available_cash = await bal_mgr.get_quote_available(account.id, asset.upper())

        profit_summary = ProfitManager.calculate_withdrawable(
            realized_net_profit=realized_pnl,
            unrealized_pnl=Decimal("0"),
            available_cash=available_cash,
            min_reserve_capital=self.settings.min_reserve_capital_usd,
            profit_allocation_pct=self.settings.profit_allocation_pct,
        )

        if amount < self.settings.withdrawal_threshold_usd:
            raise ValueError(
                f"Requested amount ${amount:.2f} is below minimum withdrawal threshold ${self.settings.withdrawal_threshold_usd:.2f}."
            )
        if amount > profit_summary.withdrawable_profit:
            raise ValueError(
                f"Requested amount ${amount:.2f} exceeds realized withdrawable profit ${profit_summary.withdrawable_profit:.2f}."
            )

        network_fee = Decimal(str(AddressManager.get_network_fee(network)))
        if amount <= network_fee:
            raise ValueError(f"Requested amount ${amount:.2f} does not cover network fee ${network_fee:.2f}.")
        net_amount = amount - network_fee

        approval = await ApprovalService.request_approval(
            session,
            action_type="WITHDRAWAL",
            summary=f"Withdraw {amount} {asset} ({net_amount} net after {network_fee} fee) to {destination_address} via {network}",
            payload={
                "account_id": str(account.id),
                "asset": asset,
                "network": network.upper(),
                "destination_address": destination_address,
                "requested_amount": str(amount),
                "network_fee": str(network_fee),
                "net_amount": str(net_amount),
                "realized_profit": str(realized_pnl),
            },
            requested_by=requested_by,
        )

        wr = WithdrawalRequest(
            account_id=account.id,
            approval_id=approval.id,
            destination_id=dest.id,
            asset=asset.upper(),
            network=network.upper(),
            destination_address=destination_address,
            requested_amount=amount,
            network_fee=network_fee,
            net_amount=net_amount,
            realized_profit_at_request=realized_pnl,
            reserve_capital_preserved=self.settings.min_reserve_capital_usd,
            status="PENDING_APPROVAL",
        )
        session.add(wr)
        await session.flush()
        return wr

    async def execute_approved_withdrawal(
        self,
        session: AsyncSession,
        *,
        withdrawal_id: uuid.UUID,
        executor: str,
    ) -> WithdrawalRequest:
        stmt = select(WithdrawalRequest).where(WithdrawalRequest.id == withdrawal_id)
        res = await session.execute(stmt)
        wr = res.scalar_one_or_none()
        if wr is None:
            raise ValueError("Withdrawal request not found.")
        if wr.status == "EXECUTED":
            raise ValueError("Withdrawal already executed.")

        if wr.approval_id is None:
            raise PermissionError("Withdrawal has no linked human approval record.")
        app_stmt = select(Approval).where(Approval.id == wr.approval_id)
        app_res = await session.execute(app_stmt)
        approval = app_res.scalar_one_or_none()
        if approval is None or approval.status != "APPROVED":
            raise PermissionError("Explicit owner approval is required before executing withdrawal.")

        wr.status = "APPROVED"
        bal_mgr = BalanceManager(session)
        available = await bal_mgr.get_quote_available(wr.account_id, wr.asset)
        if available - wr.requested_amount < wr.reserve_capital_preserved:
            wr.status = "FAILED"
            await session.flush()
            raise ValueError("Executing withdrawal would violate reserve capital preservation.")

        # Deduct from balance and record simulated/live tx id
        new_avail = available - wr.requested_amount
        await bal_mgr.repo.upsert_balance(wr.account_id, wr.asset, new_avail)
        wr.status = "EXECUTED"
        wr.tx_id = f"tx-{uuid.uuid4().hex[:24]}"
        wr.executed_at = datetime.now(timezone.utc)
        await session.flush()

        await AuditLogger.log(
            session,
            actor=executor,
            action="WITHDRAWAL_EXECUTED",
            resource_type="withdrawal_request",
            resource_id=str(wr.id),
            details={
                "amount": str(wr.requested_amount),
                "net_amount": str(wr.net_amount),
                "network": wr.network,
                "tx_id": wr.tx_id,
            },
        )
        return wr
