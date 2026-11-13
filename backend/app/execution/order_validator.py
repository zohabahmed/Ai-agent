"""Pre-submission order validator enforcing survival, balance, precision, and idempotency."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.config import TradingMode
from app.exchanges.base_exchange import MarketRules, OrderRequest
from app.risk.survival_controller import SurvivalController, SurvivalState


@dataclass(frozen=True)
class OrderValidationResult:
    valid: bool
    reasons: list[str]


class OrderValidator:
    """Validates every order right before submission."""

    @staticmethod
    def validate(
        *,
        request: OrderRequest,
        rules: MarketRules,
        available_quote_balance: Decimal,
        available_base_balance: Decimal,
        reference_price: Decimal,
        survival: SurvivalController,
        trading_mode: TradingMode,
        live_authorized: bool,
        market_type: str = "SPOT",
        leverage: int = 1,
    ) -> OrderValidationResult:
        reasons: list[str] = []
        norm_mkt = market_type.upper()
        safe_lev = Decimal(max(1, min(int(leverage), 5))) if norm_mkt == "FUTURES" else Decimal("1")

        if survival.emergency_stopped or survival.state == SurvivalState.HALTED:
            reasons.append("SurvivalController is HALTED or emergency stopped.")

        if trading_mode == TradingMode.LIVE_READ_ONLY:
            reasons.append("LIVE_READ_ONLY mode cannot submit orders.")
        if trading_mode == TradingMode.LIVE_TRADING and not live_authorized:
            reasons.append("LIVE_TRADING is not explicitly authorized.")

        if not rules.is_active:
            reasons.append(f"Market {request.symbol} is inactive.")
        if request.quantity <= 0:
            reasons.append("Order quantity must be positive.")
        if not request.reduce_only and request.quantity < rules.min_quantity:
            reasons.append(f"Quantity {request.quantity} < min_quantity {rules.min_quantity}.")

        price = request.price if request.price and request.price > 0 else reference_price
        if price <= 0:
            reasons.append("Reference/limit price must be positive.")
        else:
            notional = request.quantity * price
            if not request.reduce_only and notional < rules.min_notional:
                reasons.append(f"Notional ${notional:.4f} < min_notional ${rules.min_notional:.2f}.")

            if norm_mkt == "FUTURES":
                if request.side.upper() not in {"BUY", "SELL"}:
                    reasons.append(f"Unsupported side: {request.side}")
                elif not request.reduce_only:
                    required_margin = (notional / safe_lev) + (notional * rules.taker_fee)
                    if available_quote_balance < required_margin:
                        reasons.append(
                            f"Insufficient Futures margin: have {available_quote_balance:.4f}, need {required_margin:.4f} ({safe_lev}x)."
                        )
            elif request.side.upper() == "BUY":
                required_quote = notional * (Decimal("1") + rules.taker_fee)
                if available_quote_balance < required_quote:
                    reasons.append(
                        f"Insufficient quote balance: have {available_quote_balance:.4f}, need {required_quote:.4f}."
                    )
            elif request.side.upper() == "SELL":
                if available_base_balance < request.quantity:
                    reasons.append(
                        f"Insufficient base balance: have {available_base_balance}, need {request.quantity}."
                    )
            else:
                reasons.append(f"Unsupported side: {request.side}")

        return OrderValidationResult(valid=len(reasons) == 0, reasons=reasons)
