"""Independent Financial Survival Controller (final authority over trade execution)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any

from app.intelligence.regime_detector import MarketRegime
from app.risk.drawdown_monitor import DrawdownStatus


class SurvivalState(str, Enum):
    NORMAL = "NORMAL"
    CAUTION = "CAUTION"
    DEFENSIVE = "DEFENSIVE"
    HALTED = "HALTED"
    RECOVERY = "RECOVERY"


@dataclass(frozen=True)
class SurvivalVerdict:
    state: SurvivalState
    new_trades_allowed: bool
    exposure_scale: Decimal
    require_higher_confidence: bool
    halt_reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class SurvivalController:
    """
    Independent financial safety engine.
    Cannot be overridden by AI, strategies, or the adaptive engine.
    """

    def __init__(self) -> None:
        self.state: SurvivalState = SurvivalState.NORMAL
        self.emergency_stopped: bool = False
        self.emergency_stop_reason: str | None = None
        self.emergency_stop_at: datetime | None = None

    def trigger_emergency_stop(self, reason: str) -> dict[str, Any]:
        self.emergency_stopped = True
        self.emergency_stop_reason = reason
        self.emergency_stop_at = datetime.now(timezone.utc)
        self.state = SurvivalState.HALTED
        return {
            "emergency_stopped": True,
            "state": self.state.value,
            "reason": reason,
            "timestamp": self.emergency_stop_at.isoformat(),
            "actions": [
                "BLOCK_NEW_ORDERS",
                "CANCEL_ELIGIBLE_OPEN_ORDERS",
                "RECONCILE_BALANCES",
                "PRESERVE_EXPOSURE_RECORDS",
                "NOTIFY_OWNER",
            ],
        }

    def enter_recovery(self, *, operator_verified: bool = False) -> bool:
        if self.emergency_stopped and not operator_verified:
            return False
        self.emergency_stopped = False
        self.emergency_stop_reason = None
        self.state = SurvivalState.RECOVERY
        return True

    def evaluate(
        self,
        *,
        drawdown: DrawdownStatus,
        db_healthy: bool,
        exchange_healthy: bool,
        market_data_valid: bool,
        balance_verified: bool,
        market_regime: MarketRegime,
        strategy_deteriorated: bool = False,
        execution_error_pending: bool = False,
    ) -> SurvivalVerdict:
        halt_reasons: list[str] = []
        warnings: list[str] = []

        if self.emergency_stopped:
            halt_reasons.append(f"Emergency stop active: {self.emergency_stop_reason or 'manual halt'}")
        if not db_healthy:
            halt_reasons.append("Database unavailable: fail-safe halt.")
        if not exchange_healthy:
            halt_reasons.append("Exchange connectivity unreliable: fail-safe halt.")
        if not market_data_valid:
            halt_reasons.append("Market data invalid or stale: fail-safe halt.")
        if not balance_verified:
            halt_reasons.append("Account balance unverified: fail-safe halt.")
        if execution_error_pending:
            halt_reasons.append("Unreconciled execution error pending: halt until reconciled.")
        if drawdown.drawdown_breached:
            halt_reasons.append(
                f"MAX_DRAWDOWN breached ({drawdown.drawdown_pct:.2%} >= {drawdown.max_drawdown_limit:.2%})."
            )
        if drawdown.daily_loss_breached:
            halt_reasons.append(
                f"MAX_DAILY_LOSS breached ({drawdown.daily_loss_pct:.2%} >= {drawdown.max_daily_loss_limit:.2%})."
            )
        if market_regime == MarketRegime.ABNORMAL_UNSTABLE:
            halt_reasons.append("Market regime is ABNORMAL_UNSTABLE: halting new entries.")

        if halt_reasons:
            self.state = SurvivalState.HALTED
            return SurvivalVerdict(
                state=SurvivalState.HALTED,
                new_trades_allowed=False,
                exposure_scale=Decimal("0"),
                require_higher_confidence=True,
                halt_reasons=halt_reasons,
                warnings=warnings,
            )

        # If previously HALTED/RECOVERY and conditions cleared, step into RECOVERY first
        if self.state == SurvivalState.RECOVERY:
            warnings.append("In RECOVERY state: half exposure while validating market & strategy stability.")
            return SurvivalVerdict(
                state=SurvivalState.RECOVERY,
                new_trades_allowed=True,
                exposure_scale=Decimal("0.50"),
                require_higher_confidence=True,
                warnings=warnings,
            )

        if strategy_deteriorated:
            self.state = SurvivalState.DEFENSIVE
            warnings.append("Strategy deterioration detected: DEFENSIVE mode (0.50x exposure, strict EV).")
            return SurvivalVerdict(
                state=SurvivalState.DEFENSIVE,
                new_trades_allowed=True,
                exposure_scale=Decimal("0.50"),
                require_higher_confidence=True,
                warnings=warnings,
            )

        if drawdown.near_limit or market_regime == MarketRegime.HIGH_VOLATILITY:
            self.state = SurvivalState.CAUTION
            warnings.append("Near risk limit or HIGH_VOLATILITY regime: CAUTION mode (0.65x exposure).")
            return SurvivalVerdict(
                state=SurvivalState.CAUTION,
                new_trades_allowed=True,
                exposure_scale=Decimal("0.65"),
                require_higher_confidence=True,
                warnings=warnings,
            )

        self.state = SurvivalState.NORMAL
        return SurvivalVerdict(
            state=SurvivalState.NORMAL,
            new_trades_allowed=True,
            exposure_scale=Decimal("1.00"),
            require_higher_confidence=False,
            warnings=warnings,
        )
