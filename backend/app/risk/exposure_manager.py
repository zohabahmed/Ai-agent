"""Portfolio exposure, open-position count, and spot-only leverage enforcement."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class ExposureCheck:
    allowed: bool
    open_positions: int
    max_open_positions: int
    current_exposure_pct: Decimal
    proposed_exposure_pct: Decimal
    reasons: list[str]


class ExposureManager:
    """Enforces MAX_OPEN_POSITIONS, spot-only leverage=1, and concentration limits."""

    def __init__(
        self,
        *,
        max_open_positions: int = 1,
        max_leverage: int = 1,
        max_total_exposure_pct: Decimal = Decimal("0.95"),
        max_single_asset_pct: Decimal = Decimal("0.95"),
    ) -> None:
        self.max_open_positions = max_open_positions
        self.max_leverage = max_leverage
        self.max_total_exposure_pct = max_total_exposure_pct
        self.max_single_asset_pct = max_single_asset_pct

    def check(
        self,
        *,
        account_equity: Decimal,
        open_positions_count: int,
        current_open_notional: Decimal,
        proposed_notional: Decimal,
        requested_leverage: int = 1,
        market_type: str = "SPOT",
    ) -> ExposureCheck:
        reasons: list[str] = []
        norm_mkt = market_type.upper()
        if norm_mkt == "SPOT" and (requested_leverage > self.max_leverage or requested_leverage > 1):
            reasons.append(f"Leverage {requested_leverage}x violates spot-only limit ({self.max_leverage}x).")
        elif norm_mkt == "FUTURES" and (requested_leverage < 1 or requested_leverage > self.max_leverage or requested_leverage > 5):
            reasons.append(
                f"Futures leverage {requested_leverage}x violates survival cap (1x..{min(self.max_leverage, 5)}x)."
            )
        if open_positions_count >= self.max_open_positions:
            reasons.append(
                f"Open positions ({open_positions_count}) reached MAX_OPEN_POSITIONS ({self.max_open_positions})."
            )
        if account_equity <= 0:
            reasons.append("Account equity <= 0.")
            return ExposureCheck(
                allowed=False,
                open_positions=open_positions_count,
                max_open_positions=self.max_open_positions,
                current_exposure_pct=Decimal("0"),
                proposed_exposure_pct=Decimal("0"),
                reasons=reasons,
            )

        lev_div = Decimal(max(1, requested_leverage)) if norm_mkt == "FUTURES" else Decimal("1")
        curr_pct = current_open_notional / (account_equity * lev_div)
        prop_pct = (current_open_notional + proposed_notional) / (account_equity * lev_div)
        if prop_pct > self.max_total_exposure_pct:
            reasons.append(
                f"Margin exposure {prop_pct:.2%} exceeds max {self.max_total_exposure_pct:.2%}."
            )
        if (proposed_notional / (account_equity * lev_div)) > self.max_single_asset_pct:
            reasons.append(
                f"Single asset margin exposure exceeds {self.max_single_asset_pct:.2%}."
            )
        return ExposureCheck(
            allowed=len(reasons) == 0,
            open_positions=open_positions_count,
            max_open_positions=self.max_open_positions,
            current_exposure_pct=curr_pct.quantize(Decimal("0.0001")),
            proposed_exposure_pct=prop_pct.quantize(Decimal("0.0001")),
            reasons=reasons,
        )
