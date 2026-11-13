"""Net equity and position valuation calculator using Decimal arithmetic."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class EquitySummary:
    initial_capital: Decimal
    available_balance: Decimal
    locked_balance: Decimal
    open_position_value: Decimal
    total_equity: Decimal
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    total_fees: Decimal
    net_return_pct: Decimal


class EquityCalculator:
    """Computes exact portfolio equity from cash, open positions, realized PnL, and fees."""

    @staticmethod
    def calculate(
        *,
        initial_capital: Decimal,
        available_quote: Decimal,
        locked_quote: Decimal,
        open_positions: list[tuple[Decimal, Decimal, Decimal, Decimal]],
        # Each tuple: (quantity, entry_price, current_price, entry_fee)
        realized_pnl: Decimal,
        total_fees: Decimal,
    ) -> EquitySummary:
        open_value = Decimal("0")
        unrealized = Decimal("0")
        for qty, entry_p, curr_p, entry_fee in open_positions:
            pos_val = qty * curr_p
            open_value += pos_val
            unrealized += (curr_p - entry_p) * qty - entry_fee

        total_equity = available_quote + locked_quote + open_value
        net_return_pct = (
            ((total_equity - initial_capital) / initial_capital) * Decimal("100")
            if initial_capital > 0
            else Decimal("0")
        )
        return EquitySummary(
            initial_capital=initial_capital.quantize(Decimal("0.00000001")),
            available_balance=available_quote.quantize(Decimal("0.00000001")),
            locked_balance=locked_quote.quantize(Decimal("0.00000001")),
            open_position_value=open_value.quantize(Decimal("0.00000001")),
            total_equity=total_equity.quantize(Decimal("0.00000001")),
            realized_pnl=realized_pnl.quantize(Decimal("0.00000001")),
            unrealized_pnl=unrealized.quantize(Decimal("0.00000001")),
            total_fees=total_fees.quantize(Decimal("0.00000001")),
            net_return_pct=net_return_pct.quantize(Decimal("0.0001")),
        )

    @staticmethod
    def calculate_mixed(
        *,
        initial_capital: Decimal,
        available_quote: Decimal,
        locked_quote: Decimal,
        positions: list[tuple[Decimal, Decimal, Decimal, Decimal, str, str, Decimal]],
        # Each tuple: (quantity, entry_price, current_price, entry_fee, side, market_type, margin_used)
        realized_pnl: Decimal,
        total_fees: Decimal,
    ) -> EquitySummary:
        spot_open_val = Decimal("0")
        futures_pnl_adj = Decimal("0")
        unrealized = Decimal("0")
        open_value = Decimal("0")
        for qty, entry_p, curr_p, entry_fee, side, market_type, margin_used in positions:
            raw_pnl = (curr_p - entry_p) * qty if side.upper() != "SHORT" else (entry_p - curr_p) * qty
            pos_unrealized = raw_pnl - entry_fee
            unrealized += pos_unrealized
            if market_type.upper() == "FUTURES":
                pos_val = max(Decimal("0"), margin_used + pos_unrealized)
                open_value += pos_val
                futures_pnl_adj += pos_unrealized
            else:
                pos_val = qty * curr_p
                open_value += pos_val
                spot_open_val += pos_val

        total_equity = available_quote + locked_quote + spot_open_val + futures_pnl_adj
        net_return_pct = (
            ((total_equity - initial_capital) / initial_capital) * Decimal("100")
            if initial_capital > 0
            else Decimal("0")
        )
        return EquitySummary(
            initial_capital=initial_capital.quantize(Decimal("0.00000001")),
            available_balance=available_quote.quantize(Decimal("0.00000001")),
            locked_balance=locked_quote.quantize(Decimal("0.00000001")),
            open_position_value=open_value.quantize(Decimal("0.00000001")),
            total_equity=total_equity.quantize(Decimal("0.00000001")),
            realized_pnl=realized_pnl.quantize(Decimal("0.00000001")),
            unrealized_pnl=unrealized.quantize(Decimal("0.00000001")),
            total_fees=total_fees.quantize(Decimal("0.00000001")),
            net_return_pct=net_return_pct.quantize(Decimal("0.0001")),
        )
