"""Account & order reconciliation service."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import TradingMode
from app.database.models import Order
from app.database.repositories.trading_repo import TradingRepository
from app.exchanges.base_exchange import BaseExchange


@dataclass
class ReconciliationReport:
    verified: bool
    trading_mode: str
    discrepancies: list[str] = field(default_factory=list)
    orders_updated: int = 0
    reconciled_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class ReconciliationService:
    """Reconciles internal balances & open orders with exchange truth (or paper ledger)."""

    async def reconcile(
        self,
        *,
        session: AsyncSession,
        account_id: uuid.UUID,
        exchange: BaseExchange,
        trading_mode: TradingMode,
    ) -> ReconciliationReport:
        repo = TradingRepository(session)
        discrepancies: list[str] = []
        orders_updated = 0

        if trading_mode in {TradingMode.PAPER, TradingMode.BACKTEST}:
            # Verify internal ledger integrity: balances >= 0
            balances = await repo.get_balances(account_id)
            for bal in balances:
                if bal.available < 0 or bal.locked < 0:
                    discrepancies.append(f"Negative balance detected for {bal.asset}: avail={bal.available}")
            await repo.log_agent_event(
                event_type="RECONCILIATION",
                component="reconciliation",
                message="Paper portfolio ledger verified.",
                severity="WARNING" if discrepancies else "INFO",
                payload={"discrepancies": discrepancies},
            )
            return ReconciliationReport(
                verified=len(discrepancies) == 0,
                trading_mode=trading_mode.value,
                discrepancies=discrepancies,
            )

        # Live / Testnet reconciliation
        try:
            remote_balances = await exchange.fetch_balances()
            local_balances = {b.asset: b for b in await repo.get_balances(account_id)}
            for asset, rb in remote_balances.items():
                lb = local_balances.get(asset)
                if lb is None or abs(lb.total - rb.total) > Decimal("0.000001"):
                    discrepancies.append(
                        f"Balance drift for {asset}: local={lb.total if lb else 0}, exchange={rb.total}"
                    )
                    await repo.upsert_balance(account_id, asset, rb.free, rb.used)

            for asset, lb in local_balances.items():
                if asset not in remote_balances and lb.total > Decimal("0"):
                    discrepancies.append(
                        f"Balance cleared for {asset}: local={lb.total}, exchange=0"
                    )
                    await repo.upsert_balance(account_id, asset, Decimal("0"), Decimal("0"))

            # Reconcile only real non-simulated non-terminal orders
            stmt = select(Order).where(
                Order.account_id == account_id,
                Order.status.in_(["PENDING", "OPEN", "PARTIAL"]),
            )
            res = await session.execute(stmt)
            for order in res.scalars().all():
                is_simulated_order = (
                    (order.trading_mode or "").upper() in {TradingMode.PAPER.value, TradingMode.BACKTEST.value}
                    or (order.exchange_order_id or "").startswith("paper-")
                )
                if is_simulated_order:
                    order.status = "CANCELED"
                    orders_updated += 1
                    continue
                if order.exchange_order_id:
                    remote = await exchange.fetch_order(order.exchange_order_id, order.symbol)
                    if remote.status != order.status or remote.filled_quantity != order.filled_quantity:
                        order.status = remote.status
                        order.filled_quantity = remote.filled_quantity
                        order.average_fill_price = remote.average_price
                        order.fee_paid = remote.fee
                        orders_updated += 1
            await session.flush()
            verified = True
        except Exception as exc:
            verified = False
            discrepancies.append(f"Exchange reconciliation failed: {type(exc).__name__}")

        return ReconciliationReport(
            verified=verified,
            trading_mode=trading_mode.value,
            discrepancies=discrepancies,
            orders_updated=orders_updated,
        )
