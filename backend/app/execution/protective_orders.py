"""Exchange-side protective STOP_MARKET and TAKE_PROFIT_MARKET order synchronization."""

from __future__ import annotations

from contextlib import suppress
from decimal import Decimal, ROUND_DOWN
import logging
from typing import Any

from app.config import TradingMode
from app.exchanges.base_exchange import BaseExchange, MarketRules, OrderRequest
from app.execution.order_manager import OrderManager

logger = logging.getLogger(__name__)


def _quantize_price(value: Decimal, precision: int) -> Decimal:
    step = Decimal("10") ** (-max(0, min(8, int(precision))))
    return value.quantize(step, rounding=ROUND_DOWN)


def _quantize_qty(value: Decimal, precision: int) -> Decimal:
    step = Decimal("10") ** (-max(0, min(8, int(precision))))
    return value.quantize(step, rounding=ROUND_DOWN)


def _uses_exchange_triggers(trading_mode: TradingMode | str, market_type: str) -> bool:
    mode_val = trading_mode.value if isinstance(trading_mode, TradingMode) else str(trading_mode).upper()
    return mode_val in {TradingMode.LIVE_TRADING.value, TradingMode.TESTNET.value} and market_type.upper() == "FUTURES"


async def place_protective_orders(
    exchange: BaseExchange,
    *,
    symbol: str,
    position_side: str,
    quantity: Decimal,
    stop_loss: Decimal | None,
    take_profit: Decimal | None,
    rules: MarketRules,
    trading_mode: TradingMode | str,
    market_type: str = "FUTURES",
) -> tuple[str | None, str | None, list[str]]:
    """
    Place exchange-side reduce-only STOP_MARKET and TAKE_PROFIT_MARKET orders after entry fill.
    Returns (sl_order_id, tp_order_id, warnings).
    """
    warnings: list[str] = []
    if not _uses_exchange_triggers(trading_mode, market_type):
        return None, None, warnings

    exit_side = "BUY" if position_side.upper() == "SHORT" else "SELL"
    qty = _quantize_qty(quantity, rules.quantity_precision)
    if qty <= Decimal("0"):
        return None, None, ["Protective order skipped: quantized quantity is 0."]

    sl_id: str | None = None
    tp_id: str | None = None

    if stop_loss is not None and stop_loss > Decimal("0"):
        sl_px = _quantize_price(stop_loss, rules.price_precision)
        sl_req = OrderRequest(
            symbol=symbol,
            side=exit_side,
            order_type="STOP_MARKET",
            quantity=qty,
            stop_price=sl_px,
            client_order_id=OrderManager.build_client_order_id("slprot"),
            reduce_only=True,
        )
        try:
            res = await exchange.create_order(sl_req)
            if res.exchange_order_id and res.status != "REJECTED":
                sl_id = res.exchange_order_id
            else:
                warnings.append(f"Exchange rejected STOP_MARKET for {symbol}: status={res.status}")
        except Exception as exc:
            logger.warning("Failed to place exchange STOP_MARKET for %s: %s", symbol, exc)
            warnings.append(f"STOP_MARKET placement failed for {symbol}: {exc}")

    if take_profit is not None and take_profit > Decimal("0"):
        tp_px = _quantize_price(take_profit, rules.price_precision)
        tp_req = OrderRequest(
            symbol=symbol,
            side=exit_side,
            order_type="TAKE_PROFIT_MARKET",
            quantity=qty,
            stop_price=tp_px,
            client_order_id=OrderManager.build_client_order_id("tpprot"),
            reduce_only=True,
        )
        try:
            res = await exchange.create_order(tp_req)
            if res.exchange_order_id and res.status != "REJECTED":
                tp_id = res.exchange_order_id
            else:
                warnings.append(f"Exchange rejected TAKE_PROFIT_MARKET for {symbol}: status={res.status}")
        except Exception as exc:
            logger.warning("Failed to place exchange TAKE_PROFIT_MARKET for %s: %s", symbol, exc)
            warnings.append(f"TAKE_PROFIT_MARKET placement failed for {symbol}: {exc}")

    return sl_id, tp_id, warnings


async def sync_stop_loss_order(
    exchange: BaseExchange,
    position: Any,
    new_stop_loss: Decimal,
    *,
    rules: MarketRules,
    trading_mode: TradingMode | str,
) -> tuple[str | None, list[str]]:
    """Cancel old exchange STOP_MARKET order and place an updated reduce-only STOP_MARKET order."""
    if not _uses_exchange_triggers(trading_mode, getattr(position, "market_type", "FUTURES")):
        return getattr(position, "sl_order_id", None), []

    warnings: list[str] = []
    old_id = getattr(position, "sl_order_id", None)
    if old_id:
        with suppress(Exception):
            await exchange.cancel_order(old_id, position.symbol)

    qty = _quantize_qty(position.quantity, rules.quantity_precision)
    if qty <= Decimal("0"):
        return None, warnings

    exit_side = "BUY" if position.side.upper() == "SHORT" else "SELL"
    sl_px = _quantize_price(new_stop_loss, rules.price_precision)
    req = OrderRequest(
        symbol=position.symbol,
        side=exit_side,
        order_type="STOP_MARKET",
        quantity=qty,
        stop_price=sl_px,
        client_order_id=OrderManager.build_client_order_id("sltrail"),
        reduce_only=True,
    )
    try:
        res = await exchange.create_order(req)
        if res.exchange_order_id and res.status != "REJECTED":
            position.sl_order_id = res.exchange_order_id
            return res.exchange_order_id, warnings
        warnings.append(f"Exchange rejected replacement STOP_MARKET for {position.symbol}")
    except Exception as exc:
        logger.warning("Failed to update exchange STOP_MARKET for %s: %s", position.symbol, exc)
        warnings.append(f"STOP_MARKET update failed for {position.symbol}: {exc}")
    return getattr(position, "sl_order_id", None), warnings


async def cancel_protective_orders(
    exchange: BaseExchange,
    position: Any,
    trading_mode: TradingMode | str,
    *,
    cancel_sl: bool = True,
    cancel_tp: bool = True,
) -> None:
    """Safely cancel open exchange-side SL/TP trigger orders when position closes or updates."""
    if not _uses_exchange_triggers(trading_mode, getattr(position, "market_type", "FUTURES")):
        return

    if cancel_sl and getattr(position, "sl_order_id", None):
        with suppress(Exception):
            await exchange.cancel_order(position.sl_order_id, position.symbol)
        position.sl_order_id = None
    if cancel_tp and getattr(position, "tp_order_id", None):
        with suppress(Exception):
            await exchange.cancel_order(position.tp_order_id, position.symbol)
        position.tp_order_id = None


async def detect_triggered_protective_fill(
    exchange: BaseExchange,
    position: Any,
    trading_mode: TradingMode | str,
) -> tuple[str, Decimal] | None:
    """
    Check if exchange-side SL or TP order already triggered between polling ticks.
    Returns ('STOP_LOSS' | 'TAKE_PROFIT', fill_price) if filled, else None.
    """
    if not _uses_exchange_triggers(trading_mode, getattr(position, "market_type", "FUTURES")):
        return None

    checks = [
        ("STOP_LOSS", getattr(position, "sl_order_id", None), getattr(position, "stop_loss", None)),
        ("TAKE_PROFIT", getattr(position, "tp_order_id", None), getattr(position, "take_profit", None)),
    ]
    for label, oid, fallback_px in checks:
        if not oid:
            continue
        with suppress(Exception):
            remote = await exchange.fetch_order(oid, position.symbol)
            if remote.status == "FILLED" and remote.filled_quantity > Decimal("0"):
                fill_px = remote.average_price or fallback_px or position.current_price
                return label, Decimal(str(fill_px))
    return None
