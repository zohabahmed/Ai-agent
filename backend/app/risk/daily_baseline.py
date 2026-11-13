"""UTC daily equity baseline rollover and directional correlation helpers."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Sequence


def utc_date_str(now_utc: datetime | None = None) -> str:
    dt = now_utc or datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%d")


def sync_utc_day_baseline(
    account: Any,
    current_equity: Decimal,
    *,
    force_reset: bool = False,
    now_utc: datetime | None = None,
) -> bool:
    """
    Snapshot equity once per UTC day at 00:00 UTC rollover (or on explicit reset).
    Never uses stale lifetime initial_capital across multi-day trading sessions.
    """
    today_utc = utc_date_str(now_utc)
    stored_date = getattr(account, "day_start_date", None)
    stored_equity = Decimal(str(getattr(account, "day_start_equity", Decimal("0")) or Decimal("0")))

    if force_reset or stored_date != today_utc or stored_equity <= Decimal("0"):
        fallback = Decimal(str(getattr(account, "initial_capital", Decimal("1.00")) or Decimal("1.00")))
        baseline = current_equity if current_equity > Decimal("0") else fallback
        account.day_start_equity = baseline
        account.day_start_date = today_utc
        return True
    return False


def compute_same_side_exposure(
    open_positions: Sequence[Any],
    proposed_action: str,
) -> tuple[int, Decimal]:
    """Count open positions and total notional on the same directional side (LONG/SHORT)."""
    norm_side = "SHORT" if proposed_action.upper() in {"SELL", "SHORT"} else "LONG"
    same_count = 0
    same_notional = Decimal("0")
    for pos in open_positions:
        p_side = str(getattr(pos, "side", "LONG") or "LONG").upper()
        if p_side == norm_side:
            same_count += 1
            qty = Decimal(str(getattr(pos, "quantity", Decimal("0")) or Decimal("0")))
            px = Decimal(
                str(
                    getattr(pos, "current_price", None)
                    or getattr(pos, "entry_price", Decimal("0"))
                    or Decimal("0")
                )
            )
            same_notional += qty * px
    return same_count, same_notional
