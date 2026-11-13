"""Order book depth analysis and expected slippage calculation."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.exchanges.base_exchange import OrderBook


@dataclass(frozen=True)
class LiquidityMetrics:
    symbol: str
    best_bid: Decimal
    best_ask: Decimal
    mid_price: Decimal
    spread_bps: Decimal
    bid_depth_notional: Decimal
    ask_depth_notional: Decimal
    estimated_slippage_bps: Decimal


class OrderBookManager:
    """Analyzes order book depth, spread, and execution slippage."""

    @staticmethod
    def analyze(order_book: OrderBook, target_notional_usd: Decimal = Decimal("10.0")) -> LiquidityMetrics:
        best_bid = order_book.bids[0].price if order_book.bids else Decimal("0")
        best_ask = order_book.asks[0].price if order_book.asks else Decimal("0")
        mid = (best_bid + best_ask) / Decimal("2") if best_bid > 0 and best_ask > 0 else Decimal("0")
        spread_bps = ((best_ask - best_bid) / mid * Decimal("10000")) if mid > 0 else Decimal("0")

        bid_depth = sum((lvl.price * lvl.quantity for lvl in order_book.bids), Decimal("0"))
        ask_depth = sum((lvl.price * lvl.quantity for lvl in order_book.asks), Decimal("0"))

        slippage_bps = OrderBookManager.estimate_slippage_bps(order_book, "BUY", target_notional_usd)
        return LiquidityMetrics(
            symbol=order_book.symbol,
            best_bid=best_bid,
            best_ask=best_ask,
            mid_price=mid,
            spread_bps=spread_bps,
            bid_depth_notional=bid_depth,
            ask_depth_notional=ask_depth,
            estimated_slippage_bps=slippage_bps,
        )

    @staticmethod
    def estimate_slippage_bps(order_book: OrderBook, side: str, notional_usd: Decimal) -> Decimal:
        levels = order_book.asks if side.upper() == "BUY" else order_book.bids
        if not levels or notional_usd <= 0:
            return Decimal("0")
        ref_price = levels[0].price
        if ref_price <= 0:
            return Decimal("0")

        remaining = notional_usd
        total_qty = Decimal("0")
        total_cost = Decimal("0")
        for lvl in levels:
            lvl_notional = lvl.price * lvl.quantity
            take_notional = min(remaining, lvl_notional)
            qty = take_notional / lvl.price
            total_qty += qty
            total_cost += take_notional
            remaining -= take_notional
            if remaining <= 0:
                break

        if total_qty <= 0:
            return Decimal("50.0")
        vwap = total_cost / total_qty
        slip_pct = abs(vwap - ref_price) / ref_price
        if remaining > 0:
            # Insufficient book depth penalty
            slip_pct += Decimal("0.005")
        return (slip_pct * Decimal("10000")).quantize(Decimal("0.01"))
