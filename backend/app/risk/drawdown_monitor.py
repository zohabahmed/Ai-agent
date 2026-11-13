"""Drawdown and daily loss monitoring."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class DrawdownStatus:
    peak_equity: Decimal
    current_equity: Decimal
    drawdown_pct: Decimal
    daily_loss_pct: Decimal
    max_drawdown_limit: Decimal
    max_daily_loss_limit: Decimal
    drawdown_breached: bool
    daily_loss_breached: bool
    near_limit: bool


class DrawdownMonitor:
    """Monitors peak-to-trough drawdown and daily loss against hard survival limits."""

    def __init__(self, max_drawdown: Decimal, max_daily_loss: Decimal) -> None:
        self.max_drawdown = max_drawdown
        self.max_daily_loss = max_daily_loss

    def evaluate(
        self,
        *,
        peak_equity: Decimal,
        current_equity: Decimal,
        day_start_equity: Decimal,
    ) -> DrawdownStatus:
        effective_peak = max(peak_equity, current_equity, Decimal("0.000001"))
        dd_pct = max(Decimal("0"), (effective_peak - current_equity) / effective_peak)

        if day_start_equity > 0:
            daily_loss_pct = max(Decimal("0"), (day_start_equity - current_equity) / day_start_equity)
        else:
            daily_loss_pct = Decimal("0")

        # For micro balances (e.g. <= $25.00), normal exchange fees and tick noise (5-10 cents)
        # equal 1-2% of total equity. Enforce sensible USD floors so cents of fee noise never trigger halts.
        is_micro_balance = day_start_equity <= Decimal("25.00") or effective_peak <= Decimal("25.00")
        daily_loss_usd = max(Decimal("0"), day_start_equity - current_equity)
        drawdown_usd = max(Decimal("0"), effective_peak - current_equity)

        if is_micro_balance:
            eff_max_daily = max(self.max_daily_loss, Decimal("0.15"))
            eff_max_dd = max(self.max_drawdown, Decimal("0.25"))
            daily_breached = daily_loss_pct >= eff_max_daily and daily_loss_usd >= Decimal("0.75")
            dd_breached = dd_pct >= eff_max_dd and drawdown_usd >= Decimal("1.25")
            near_limit = (
                (daily_loss_pct >= eff_max_daily * Decimal("0.60") and daily_loss_usd >= Decimal("0.45"))
                or (dd_pct >= eff_max_dd * Decimal("0.60") and drawdown_usd >= Decimal("0.75"))
            )
        else:
            dd_breached = dd_pct >= self.max_drawdown
            daily_breached = daily_loss_pct >= self.max_daily_loss
            near_limit = (
                dd_pct >= self.max_drawdown * Decimal("0.60")
                or daily_loss_pct >= self.max_daily_loss * Decimal("0.60")
            )
        return DrawdownStatus(
            peak_equity=effective_peak,
            current_equity=current_equity,
            drawdown_pct=dd_pct.quantize(Decimal("0.000001")),
            daily_loss_pct=daily_loss_pct.quantize(Decimal("0.000001")),
            max_drawdown_limit=self.max_drawdown,
            max_daily_loss_limit=self.max_daily_loss,
            drawdown_breached=dd_breached,
            daily_loss_breached=daily_breached,
            near_limit=near_limit,
        )
