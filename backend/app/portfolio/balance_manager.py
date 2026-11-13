"""Balance manager using Decimal precision."""

from __future__ import annotations

from decimal import Decimal
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Balance
from app.database.repositories.trading_repo import TradingRepository


class BalanceManager:
    """Manages available, locked, and total asset balances."""

    def __init__(self, session: AsyncSession) -> None:
        self.repo = TradingRepository(session)

    async def get_all(self, account_id: uuid.UUID) -> list[Balance]:
        return await self.repo.get_balances(account_id)

    async def get_quote_available(self, account_id: uuid.UUID, quote_asset: str = "USDT") -> Decimal:
        balances = await self.repo.get_balances(account_id)
        for b in balances:
            if b.asset == quote_asset:
                return b.available
        return Decimal("0")

    async def get_quote_locked(self, account_id: uuid.UUID, quote_asset: str = "USDT") -> Decimal:
        balances = await self.repo.get_balances(account_id)
        for b in balances:
            if b.asset == quote_asset:
                return b.locked
        return Decimal("0")
