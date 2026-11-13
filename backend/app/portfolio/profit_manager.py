"""Profit & withdrawable reserve calculation (never counts unrealized gains as withdrawable)."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class ProfitSummary:
    realized_net_profit: Decimal
    unrealized_pnl: Decimal
    available_cash: Decimal
    reserve_capital_required: Decimal
    withdrawable_profit: Decimal
    profit_allocation_pct: Decimal


class ProfitManager:
    """Separates realized net profit from unrealized gains and enforces reserve capital."""

    @staticmethod
    def calculate_withdrawable(
        *,
        realized_net_profit: Decimal,
        unrealized_pnl: Decimal,
        available_cash: Decimal,
        min_reserve_capital: Decimal,
        profit_allocation_pct: Decimal,
    ) -> ProfitSummary:
        # Never count unrealized gains as withdrawable profit!
        positive_realized = max(Decimal("0"), realized_net_profit)
        allocated_profit = positive_realized * profit_allocation_pct
        excess_cash = max(Decimal("0"), available_cash - min_reserve_capital)
        withdrawable = min(allocated_profit, excess_cash)

        return ProfitSummary(
            realized_net_profit=realized_net_profit.quantize(Decimal("0.00000001")),
            unrealized_pnl=unrealized_pnl.quantize(Decimal("0.00000001")),
            available_cash=available_cash.quantize(Decimal("0.00000001")),
            reserve_capital_required=min_reserve_capital.quantize(Decimal("0.00000001")),
            withdrawable_profit=withdrawable.quantize(Decimal("0.00000001")),
            profit_allocation_pct=profit_allocation_pct,
        )
