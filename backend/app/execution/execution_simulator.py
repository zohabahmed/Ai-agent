"""Realistic Paper Execution Simulator (bid/ask spread, slippage, partial fills, fees, SL/TP)."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN
import uuid

from app.exchanges.base_exchange import (
    MarketRules,
    OrderBook,
    OrderRequest,
    OrderResult,
    Ticker,
)
from app.market.orderbook_manager import OrderBookManager


class PaperExecutionSimulator:
    """
    Simulates realistic spot order execution.
    - BUY market orders cross the ask + slippage; SELL market orders cross the bid - slippage.
    - LIMIT orders fill only if market price crosses the limit price.
    - Supports deterministic/liquidity-based partial fills and exchange fees.
    """

    def __init__(
        self,
        *,
        default_slippage_bps: Decimal = Decimal("4.0"),
        partial_fill_liquidity_ratio: Decimal = Decimal("0.80"),
    ) -> None:
        self.default_slippage_bps = default_slippage_bps
        self.partial_fill_liquidity_ratio = partial_fill_liquidity_ratio

    def execute_order(
        self,
        *,
        request: OrderRequest,
        ticker: Ticker,
        rules: MarketRules,
        order_book: OrderBook | None = None,
        available_quote_balance: Decimal = Decimal("0"),
        available_base_balance: Decimal = Decimal("0"),
        force_fill_ratio: Decimal | None = None,
        market_type: str = "SPOT",
        leverage: int = 1,
    ) -> OrderResult:
        now = datetime.now(timezone.utc)
        side = request.side.upper()
        order_type = request.order_type.upper()

        # Check exchange minimums (reduce-only closes / partial profit-booking are exempt so positions can always exit)
        ref_price = ticker.ask if side == "BUY" else ticker.bid
        if ref_price <= 0:
            ref_price = ticker.last

        if not request.reduce_only and (
            request.quantity < rules.min_quantity or (request.quantity * ref_price) < rules.min_notional
        ):
            return OrderResult(
                exchange_order_id=f"paper-rej-{uuid.uuid4().hex[:12]}",
                client_order_id=request.client_order_id,
                symbol=request.symbol,
                side=side,
                order_type=order_type,
                status="REJECTED",
                requested_quantity=request.quantity,
                filled_quantity=Decimal("0"),
                average_price=None,
                fee=Decimal("0"),
                fee_asset=rules.quote_asset,
                timestamp=now,
                raw={"reason": "Below exchange minimum quantity or notional"},
            )

        # Limit order price check
        if order_type == "LIMIT":
            if request.price is None or request.price <= 0:
                return OrderResult(
                    exchange_order_id=f"paper-rej-{uuid.uuid4().hex[:12]}",
                    client_order_id=request.client_order_id,
                    symbol=request.symbol,
                    side=side,
                    order_type=order_type,
                    status="REJECTED",
                    requested_quantity=request.quantity,
                    filled_quantity=Decimal("0"),
                    average_price=None,
                    fee=Decimal("0"),
                    fee_asset=rules.quote_asset,
                    timestamp=now,
                    raw={"reason": "Invalid limit price"},
                )
            if (side == "BUY" and ticker.ask > request.price) or (side == "SELL" and ticker.bid < request.price):
                return OrderResult(
                    exchange_order_id=f"paper-open-{uuid.uuid4().hex[:12]}",
                    client_order_id=request.client_order_id,
                    symbol=request.symbol,
                    side=side,
                    order_type=order_type,
                    status="OPEN",
                    requested_quantity=request.quantity,
                    filled_quantity=Decimal("0"),
                    average_price=None,
                    fee=Decimal("0"),
                    fee_asset=rules.quote_asset,
                    timestamp=now,
                    raw={"reason": "Limit price not yet reached"},
                )
            exec_price = request.price
            fee_rate = rules.maker_fee
        else:
            slippage_bps = self.default_slippage_bps
            if order_book is not None:
                slippage_bps = max(
                    self.default_slippage_bps,
                    OrderBookManager.estimate_slippage_bps(order_book, side, request.quantity * ref_price),
                )
            slip_mult = slippage_bps / Decimal("10000")
            exec_price = ref_price * (Decimal("1") + slip_mult) if side == "BUY" else ref_price * (Decimal("1") - slip_mult)
            fee_rate = rules.taker_fee

        price_quant = Decimal("1").scaleb(-rules.price_precision)
        exec_price = exec_price.quantize(price_quant)

        # Partial fill simulation based on top-of-book depth or explicit test override
        fill_ratio = Decimal("1.0")
        if force_fill_ratio is not None:
            fill_ratio = max(Decimal("0"), min(Decimal("1"), force_fill_ratio))
        elif order_book is not None:
            levels = order_book.asks if side == "BUY" else order_book.bids
            avail_qty = sum((lvl.quantity for lvl in levels[:3]), Decimal("0"))
            if avail_qty > 0 and request.quantity > avail_qty * self.partial_fill_liquidity_ratio:
                fill_ratio = (avail_qty * self.partial_fill_liquidity_ratio) / request.quantity
                fill_ratio = max(Decimal("0.10"), min(Decimal("1.0"), fill_ratio))

        qty_quant = Decimal("1").scaleb(-rules.quantity_precision)
        filled_qty = (request.quantity * fill_ratio).quantize(qty_quant, rounding=ROUND_DOWN)
        if filled_qty <= 0:
            return OrderResult(
                exchange_order_id=f"paper-rej-{uuid.uuid4().hex[:12]}",
                client_order_id=request.client_order_id,
                symbol=request.symbol,
                side=side,
                order_type=order_type,
                status="REJECTED",
                requested_quantity=request.quantity,
                filled_quantity=Decimal("0"),
                average_price=None,
                fee=Decimal("0"),
                fee_asset=rules.quote_asset,
                timestamp=now,
                raw={"reason": "Zero rounded fill quantity"},
            )

        notional = filled_qty * exec_price
        fee = (notional * fee_rate).quantize(Decimal("0.00000001"))
        norm_mkt = market_type.upper()

        if norm_mkt == "FUTURES":
            if not request.reduce_only:
                safe_lev = Decimal(max(1, min(int(leverage), 5)))
                req_margin = (notional / safe_lev) + fee
                if req_margin > available_quote_balance:
                    return OrderResult(
                        exchange_order_id=f"paper-rej-{uuid.uuid4().hex[:12]}",
                        client_order_id=request.client_order_id,
                        symbol=request.symbol,
                        side=side,
                        order_type=order_type,
                        status="REJECTED",
                        requested_quantity=request.quantity,
                        filled_quantity=Decimal("0"),
                        average_price=None,
                        fee=Decimal("0"),
                        fee_asset=rules.quote_asset,
                        timestamp=now,
                        raw={"reason": "Insufficient paper Futures margin"},
                    )
        else:
            if side == "BUY" and (notional + fee) > available_quote_balance:
                return OrderResult(
                    exchange_order_id=f"paper-rej-{uuid.uuid4().hex[:12]}",
                    client_order_id=request.client_order_id,
                    symbol=request.symbol,
                    side=side,
                    order_type=order_type,
                    status="REJECTED",
                    requested_quantity=request.quantity,
                    filled_quantity=Decimal("0"),
                    average_price=None,
                    fee=Decimal("0"),
                    fee_asset=rules.quote_asset,
                    timestamp=now,
                    raw={"reason": "Insufficient paper quote balance"},
                )
            if side == "SELL" and filled_qty > available_base_balance:
                return OrderResult(
                    exchange_order_id=f"paper-rej-{uuid.uuid4().hex[:12]}",
                    client_order_id=request.client_order_id,
                    symbol=request.symbol,
                    side=side,
                    order_type=order_type,
                    status="REJECTED",
                    requested_quantity=request.quantity,
                    filled_quantity=Decimal("0"),
                    average_price=None,
                    fee=Decimal("0"),
                    fee_asset=rules.quote_asset,
                    timestamp=now,
                    raw={"reason": "Insufficient paper base balance"},
                )

        status = "FILLED" if filled_qty >= request.quantity else "PARTIAL"
        return OrderResult(
            exchange_order_id=f"paper-{uuid.uuid4().hex[:16]}",
            client_order_id=request.client_order_id,
            symbol=request.symbol,
            side=side,
            order_type=order_type,
            status=status,
            requested_quantity=request.quantity,
            filled_quantity=filled_qty,
            average_price=exec_price,
            fee=fee,
            fee_asset=rules.quote_asset,
            timestamp=now,
            raw={"simulated": True, "fill_ratio": str(fill_ratio), "fee_rate": str(fee_rate)},
        )
