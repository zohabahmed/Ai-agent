"""Stop-loss-based position sizer respecting exchange minimums without inflating risk."""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN

from app.exchanges.base_exchange import MarketRules


def effective_isolated_leverage(
    base_leverage: int,
    available_balance: Decimal,
    rules: MarketRules,
    *,
    reference_price: Decimal | None = None,
    max_cap: int = 5,
) -> int:
    """
    Use base leverage (1x) whenever possible; if a small Futures wallet cannot clear the venue
    order minimum at 1x, return the lowest safe integer isolated leverage (up to max_cap=5x).
    """
    safe_base = max(1, min(int(base_leverage), max_cap))
    if available_balance <= 0:
        return safe_base
    min_req = rules.min_notional
    if reference_price is not None and reference_price > 0 and rules.min_quantity > 0:
        min_req = max(min_req, rules.min_quantity * reference_price)
    if min_req <= 0:
        return safe_base
    # Require a ~6% isolated margin + fee/rounding cushion over venue min notional
    safe_usable = available_balance * Decimal("0.93")
    if safe_usable * Decimal(safe_base) >= min_req:
        return safe_base
    needed = math.ceil(float((min_req * Decimal("1.04")) / safe_usable)) if safe_usable > 0 else max_cap
    return max(safe_base, min(max_cap, needed))


@dataclass(frozen=True)
class PositionSizeResult:
    is_viable: bool
    risk_budget_usd: Decimal
    target_notional_usd: Decimal
    capped_notional_usd: Decimal
    quantity: Decimal
    estimated_entry_fee_usd: Decimal
    reason: str


class PositionSizer:
    """
    Separates risk capital from order notional:
      Allowed loss = Account equity * Kelly risk fraction
      Position notional = Allowed loss / Stop distance %
    Never increases risk budget to satisfy an exchange minimum notional or minimum quantity.
    """

    @staticmethod
    def round_down(value: Decimal, decimals: int) -> Decimal:
        quant = Decimal("1").scaleb(-decimals)
        return value.quantize(quant, rounding=ROUND_DOWN)

    def calculate(
        self,
        *,
        account_equity: Decimal,
        available_balance: Decimal,
        kelly_risk_fraction: Decimal,
        entry_price: Decimal,
        stop_loss_price: Decimal,
        rules: MarketRules,
        max_exposure_pct: Decimal = Decimal("0.95"),
        regime_scale: Decimal = Decimal("1.0"),
        slippage_pct: Decimal = Decimal("0.001"),
        leverage: int = 1,
    ) -> PositionSizeResult:
        if account_equity <= 0 or available_balance <= 0 or kelly_risk_fraction <= 0:
            return PositionSizeResult(
                is_viable=False,
                risk_budget_usd=Decimal("0"),
                target_notional_usd=Decimal("0"),
                capped_notional_usd=Decimal("0"),
                quantity=Decimal("0"),
                estimated_entry_fee_usd=Decimal("0"),
                reason="Zero equity, available balance, or Kelly risk fraction.",
            )

        if entry_price <= 0 or stop_loss_price <= 0 or entry_price == stop_loss_price:
            return PositionSizeResult(
                is_viable=False,
                risk_budget_usd=Decimal("0"),
                target_notional_usd=Decimal("0"),
                capped_notional_usd=Decimal("0"),
                quantity=Decimal("0"),
                estimated_entry_fee_usd=Decimal("0"),
                reason="Invalid entry or stop-loss price.",
            )

        stop_distance_pct = abs(entry_price - stop_loss_price) / entry_price
        if stop_distance_pct <= Decimal("0.0005"):
            return PositionSizeResult(
                is_viable=False,
                risk_budget_usd=Decimal("0"),
                target_notional_usd=Decimal("0"),
                capped_notional_usd=Decimal("0"),
                quantity=Decimal("0"),
                estimated_entry_fee_usd=Decimal("0"),
                reason="Stop distance too tight (<0.05%) for safe spot execution.",
            )

        # Allowed loss = Equity * Kelly fraction * regime safety scale
        risk_budget = account_equity * kelly_risk_fraction * regime_scale
        target_notional = risk_budget / stop_distance_pct

        # Cap by available margin/cash (minus fee & slippage buffer) and portfolio exposure limit
        safe_lev = Decimal(max(1, min(int(leverage), 5)))
        fee_buffer = Decimal("1") + (rules.taker_fee + slippage_pct) * safe_lev
        max_affordable_notional = (available_balance * safe_lev) / fee_buffer
        max_equity_notional = account_equity * max_exposure_pct * safe_lev
        capped_notional = min(target_notional, max_affordable_notional, max_equity_notional)

        raw_qty = capped_notional / entry_price
        if rules.lot_step is not None and rules.lot_step > 0:
            steps = (raw_qty / rules.lot_step).to_integral_value(rounding=ROUND_DOWN)
            qty = (steps * rules.lot_step).quantize(
                Decimal("1").scaleb(-max(0, rules.quantity_precision)),
                rounding=ROUND_DOWN,
            )
        else:
            qty = self.round_down(raw_qty, rules.quantity_precision)
        actual_notional = qty * entry_price
        est_fee = actual_notional * rules.taker_fee

        # Strictly reject if below exchange minimums (never inflate risk budget to meet minimums!)
        if qty < rules.min_quantity:
            return PositionSizeResult(
                is_viable=False,
                risk_budget_usd=risk_budget,
                target_notional_usd=target_notional,
                capped_notional_usd=actual_notional,
                quantity=qty,
                estimated_entry_fee_usd=est_fee,
                reason=(
                    f"Economically non-viable: computed qty {qty} < exchange min_quantity {rules.min_quantity} "
                    f"(risk budget ${risk_budget:.4f}, equity ${account_equity:.2f}). Capital preserved."
                ),
            )

        if actual_notional < rules.min_notional:
            return PositionSizeResult(
                is_viable=False,
                risk_budget_usd=risk_budget,
                target_notional_usd=target_notional,
                capped_notional_usd=actual_notional,
                quantity=qty,
                estimated_entry_fee_usd=est_fee,
                reason=(
                    f"Economically non-viable: order notional ${actual_notional:.4f} < exchange min_notional "
                    f"${rules.min_notional:.2f} (risk budget ${risk_budget:.4f}). Capital preserved."
                ),
            )

        return PositionSizeResult(
            is_viable=True,
            risk_budget_usd=risk_budget.quantize(Decimal("0.000001")),
            target_notional_usd=target_notional.quantize(Decimal("0.000001")),
            capped_notional_usd=actual_notional.quantize(Decimal("0.000001")),
            quantity=qty,
            estimated_entry_fee_usd=est_fee.quantize(Decimal("0.000001")),
            reason=f"Viable order: qty={qty}, notional=${actual_notional:.4f}, risk=${risk_budget:.4f}",
        )
