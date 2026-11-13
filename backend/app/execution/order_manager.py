"""Idempotent Order Manager preventing duplicate order submissions."""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Order
from app.exchanges.base_exchange import OrderResult


class OrderManager:
    """Creates idempotent client order IDs and persists order state transitions."""

    @staticmethod
    def build_client_order_id(prefix: str = "surv") -> str:
        return f"{prefix}-{uuid.uuid4().hex[:20]}"

    @staticmethod
    async def get_by_client_order_id(session: AsyncSession, client_order_id: str) -> Order | None:
        stmt = select(Order).where(Order.client_order_id == client_order_id)
        res = await session.execute(stmt)
        return res.scalar_one_or_none()

    @staticmethod
    async def create_pending_order(
        session: AsyncSession,
        *,
        account_id: uuid.UUID,
        risk_decision_id: uuid.UUID | None,
        client_order_id: str,
        exchange: str,
        symbol: str,
        side: str,
        order_type: str,
        trading_mode: str,
        quantity: Decimal,
        limit_price: Decimal | None,
        strategy_name: str,
        stop_loss: Decimal | None = None,
        take_profit: Decimal | None = None,
    ) -> tuple[Order, bool]:
        existing = await OrderManager.get_by_client_order_id(session, client_order_id)
        if existing is not None:
            return existing, False

        order = Order(
            account_id=account_id,
            risk_decision_id=risk_decision_id,
            client_order_id=client_order_id,
            exchange=exchange,
            symbol=symbol,
            side=side.upper(),
            order_type=order_type.upper(),
            trading_mode=trading_mode,
            status="PENDING",
            requested_quantity=quantity,
            filled_quantity=Decimal("0"),
            limit_price=limit_price,
            strategy_name=strategy_name,
            stop_loss=stop_loss,
            take_profit=take_profit,
        )
        session.add(order)
        await session.flush()
        return order, True

    @staticmethod
    async def apply_execution_result(
        session: AsyncSession,
        order: Order,
        result: OrderResult,
        error_message: str | None = None,
    ) -> Order:
        order.exchange_order_id = result.exchange_order_id or order.exchange_order_id
        order.status = result.status
        order.filled_quantity = result.filled_quantity
        order.average_fill_price = result.average_price
        order.fee_paid = result.fee
        order.fee_asset = result.fee_asset
        order.error_message = error_message
        await session.flush()
        return order
