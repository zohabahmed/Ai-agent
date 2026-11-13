"""Execution Engine routing validated orders to Paper Simulator or Exchange Adapter."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, TradingMode, get_settings
from app.database.models import Order, Position, Trade
from app.database.repositories.trading_repo import TradingRepository
from app.exchanges.base_exchange import (
    BaseExchange,
    ExchangeError,
    MarketRules,
    OrderBook,
    OrderRequest,
    OrderResult,
    Ticker,
)
from app.execution.execution_simulator import PaperExecutionSimulator
from app.execution.order_manager import OrderManager
from app.execution.order_validator import OrderValidator
from app.risk.survival_controller import SurvivalController


@dataclass
class ExecutionOutcome:
    executed: bool
    duplicate_skipped: bool
    ambiguous_error: bool
    order: Order | None
    result: OrderResult | None
    reason: str


class ExecutionEngine:
    """Executes risk-approved orders idempotently across PAPER, TESTNET, or LIVE_TRADING."""

    def __init__(
        self,
        *,
        exchange: BaseExchange,
        survival: SurvivalController,
        simulator: PaperExecutionSimulator | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.exchange = exchange
        self.survival = survival
        self.simulator = simulator or PaperExecutionSimulator()
        self.settings = settings or get_settings()

    async def execute(
        self,
        *,
        session: AsyncSession,
        account_id: uuid.UUID,
        risk_decision_id: uuid.UUID | None,
        request: OrderRequest,
        rules: MarketRules,
        ticker: Ticker,
        order_book: OrderBook | None,
        trading_mode: TradingMode,
        live_authorized: bool,
        strategy_name: str,
        market_regime: str,
        stop_loss: Decimal | None = None,
        take_profit: Decimal | None = None,
        force_fill_ratio: Decimal | None = None,
        market_type: str = "SPOT",
        leverage: int = 1,
        closing_position: Position | None = None,
    ) -> ExecutionOutcome:
        norm_mkt = market_type.upper()
        safe_lev = max(1, min(int(leverage), 5)) if norm_mkt == "FUTURES" else 1
        repo = TradingRepository(session)
        balances = {b.asset: b for b in await repo.get_balances(account_id)}
        quote_bal = balances.get(rules.quote_asset)
        base_bal = balances.get(rules.base_asset)
        avail_quote = quote_bal.available if quote_bal else Decimal("0")
        locked_quote = quote_bal.locked if quote_bal else Decimal("0")
        avail_base = base_bal.available if base_bal else Decimal("0")
        is_simulated = trading_mode in {TradingMode.PAPER, TradingMode.BACKTEST}
        if (
            closing_position is not None
            and is_simulated
            and norm_mkt != "FUTURES"
            and request.side.upper() == "SELL"
        ):
            # Allow closing legacy simulated SPOT positions even if base ledger was cleared by exchange sync
            avail_base = max(avail_base, request.quantity)

        validation = OrderValidator.validate(
            request=request,
            rules=rules,
            available_quote_balance=avail_quote,
            available_base_balance=avail_base,
            reference_price=ticker.last,
            survival=self.survival,
            trading_mode=trading_mode,
            live_authorized=live_authorized,
            market_type=norm_mkt,
            leverage=safe_lev,
        )
        if not validation.valid:
            return ExecutionOutcome(
                executed=False,
                duplicate_skipped=False,
                ambiguous_error=False,
                order=None,
                result=None,
                reason="; ".join(validation.reasons),
            )

        order, created = await OrderManager.create_pending_order(
            session,
            account_id=account_id,
            risk_decision_id=risk_decision_id,
            client_order_id=request.client_order_id,
            exchange=self.exchange.exchange_id,
            symbol=request.symbol,
            side=request.side,
            order_type=request.order_type,
            trading_mode=trading_mode.value,
            quantity=request.quantity,
            limit_price=request.price,
            strategy_name=strategy_name,
            stop_loss=stop_loss,
            take_profit=take_profit,
        )
        if not created:
            return ExecutionOutcome(
                executed=False,
                duplicate_skipped=True,
                ambiguous_error=False,
                order=order,
                result=None,
                reason=f"Duplicate client_order_id {request.client_order_id} skipped.",
            )

        try:
            if is_simulated:
                result = self.simulator.execute_order(
                    request=request,
                    ticker=ticker,
                    rules=rules,
                    order_book=order_book,
                    available_quote_balance=avail_quote,
                    available_base_balance=avail_base,
                    force_fill_ratio=force_fill_ratio,
                    market_type=norm_mkt,
                    leverage=safe_lev,
                )
            else:
                if norm_mkt == "FUTURES" and not request.reduce_only:
                    await self.exchange.configure_futures_position(
                        request.symbol, safe_lev, margin_mode="isolated"
                    )
                result = await self.exchange.create_order(request)
        except ExchangeError as exc:
            err_msg = str(exc)
            lowered = err_msg.lower()
            deterministic_reject = any(
                marker in lowered
                for marker in (
                    "insufficientfunds",
                    "insufficient balance",
                    "margin is insufficient",
                    "-2019",
                    "-2010",
                    "read-only mode",
                    "below exchange minimum",
                    "invalidorder",
                    "min_notional",
                    "precision",
                    "permissiondenied",
                    "not configured",
                )
            )
            if deterministic_reject:
                order.status = "REJECTED"
                order.error_message = err_msg
                await session.flush()
                return ExecutionOutcome(
                    executed=False,
                    duplicate_skipped=False,
                    ambiguous_error=False,
                    order=order,
                    result=None,
                    reason=f"Exchange rejected order: {err_msg}",
                )
            order.status = "OPEN"  # Ambiguous state: require reconciliation before retry
            order.error_message = err_msg
            await session.flush()
            return ExecutionOutcome(
                executed=False,
                duplicate_skipped=False,
                ambiguous_error=True,
                order=order,
                result=None,
                reason=f"Ambiguous exchange execution error; reconciliation required: {exc}",
            )

        await OrderManager.apply_execution_result(session, order, result)

        if result.filled_quantity > 0 and result.average_price is not None:
            notional = result.filled_quantity * result.average_price
            ref = ticker.ask if request.side.upper() == "BUY" else ticker.bid
            slippage_bps = (
                (abs(result.average_price - ref) / ref) * Decimal("10000")
                if ref > 0
                else Decimal("0")
            )
            trade = Trade(
                account_id=account_id,
                order_id=order.id,
                exchange=self.exchange.exchange_id,
                exchange_trade_id=f"{result.exchange_order_id}-fill",
                symbol=request.symbol,
                side=request.side.upper(),
                trading_mode=trading_mode.value,
                strategy_name=strategy_name,
                market_regime=market_regime,
                quantity=result.filled_quantity,
                price=result.average_price,
                notional=notional,
                fee=result.fee,
                slippage_bps=slippage_bps.quantize(Decimal("0.01")),
                is_simulated=is_simulated,
            )
            session.add(trade)

            # Update internal ledger balances for paper/simulated mode
            if is_simulated:
                if norm_mkt == "FUTURES":
                    if closing_position is None:
                        margin = notional / Decimal(safe_lev)
                        new_quote = max(Decimal("0"), avail_quote - margin - result.fee)
                        new_locked = locked_quote + margin
                    else:
                        if closing_position.side == "SHORT":
                            gross_pnl = (closing_position.entry_price - result.average_price) * result.filled_quantity
                        else:
                            gross_pnl = (result.average_price - closing_position.entry_price) * result.filled_quantity
                        released = closing_position.margin_used
                        new_quote = max(Decimal("0"), avail_quote + released + gross_pnl - result.fee)
                        new_locked = max(Decimal("0"), locked_quote - released)
                    await repo.upsert_balance(account_id, rules.quote_asset, new_quote, new_locked)
                else:
                    if request.side.upper() == "BUY":
                        new_quote = max(Decimal("0"), avail_quote - notional - result.fee)
                        new_base = avail_base + result.filled_quantity
                    else:
                        new_quote = avail_quote + notional - result.fee
                        new_base = max(Decimal("0"), avail_base - result.filled_quantity)
                    await repo.upsert_balance(account_id, rules.quote_asset, new_quote)
                    await repo.upsert_balance(account_id, rules.base_asset, new_base)
            await session.flush()

        return ExecutionOutcome(
            executed=result.status in {"FILLED", "PARTIAL"},
            duplicate_skipped=False,
            ambiguous_error=False,
            order=order,
            result=result,
            reason=f"Order {result.status}: filled {result.filled_quantity}/{request.quantity}",
        )
