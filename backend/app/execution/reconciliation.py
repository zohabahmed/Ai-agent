"""Account & order reconciliation service."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import TradingMode
from app.database.models import Account, Order, Position, RiskDecision
from app.database.repositories.trading_repo import TradingRepository
from app.exchanges.base_exchange import BaseExchange, ExchangePosition


@dataclass
class LivePositionSyncStats:
    imported: int = 0
    updated: int = 0
    closed: int = 0
    orders_rejected: int = 0
    open_positions_count: int = 0
    total_margin_used: Decimal = Decimal("0")
    total_unrealized_pnl: Decimal = Decimal("0")


@dataclass
class ReconciliationReport:
    verified: bool
    trading_mode: str
    discrepancies: list[str] = field(default_factory=list)
    orders_updated: int = 0
    positions_synced: int = 0
    reconciled_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class ReconciliationService:
    """Reconciles internal balances, open orders, and live Futures positions with exchange truth."""

    @staticmethod
    async def sync_live_positions(
        *,
        session: AsyncSession,
        account: Account,
        exchange: BaseExchange,
        remote_positions: list[ExchangePosition] | None = None,
    ) -> LivePositionSyncStats:
        stats = LivePositionSyncStats()
        if account.trading_mode in {TradingMode.PAPER.value, TradingMode.BACKTEST.value}:
            return stats

        # Clean up dead failed-submission orders that stayed OPEN with zero exchange order id
        failed_orders_stmt = select(Order).where(
            Order.account_id == account.id,
            Order.status.in_(["PENDING", "OPEN", "PARTIAL"]),
            Order.exchange_order_id.is_(None),
            Order.error_message.is_not(None),
        )
        failed_res = await session.execute(failed_orders_stmt)
        for bad_ord in failed_res.scalars().all():
            if bad_ord.filled_quantity <= Decimal("0") and (bad_ord.error_message or "").strip():
                bad_ord.status = "REJECTED"
                stats.orders_rejected += 1

        if (account.market_type or "").upper() != "FUTURES":
            await session.flush()
            return stats

        if remote_positions is None:
            remote_positions = await exchange.fetch_positions()

        remote_by_key: dict[tuple[str, str], ExchangePosition] = {
            (rp.symbol.upper(), rp.side.upper()): rp for rp in remote_positions
        }

        local_stmt = select(Position).where(
            Position.account_id == account.id,
            Position.status == "OPEN",
            Position.trading_mode.not_in([TradingMode.PAPER.value, TradingMode.BACKTEST.value]),
        )
        local_res = await session.execute(local_stmt)
        local_open = list(local_res.scalars().all())
        matched_keys: set[tuple[str, str]] = set()
        now = datetime.now(timezone.utc)

        for lp in local_open:
            if (lp.market_type or "").upper() != "FUTURES":
                continue
            key = (lp.symbol.upper(), lp.side.upper())
            rp = remote_by_key.get(key)
            if rp is not None and key not in matched_keys:
                matched_keys.add(key)
                lp.quantity = rp.quantity
                if rp.entry_price > Decimal("0"):
                    lp.entry_price = rp.entry_price
                lp.current_price = rp.mark_price if rp.mark_price > Decimal("0") else lp.entry_price
                if rp.margin_used > Decimal("0"):
                    lp.margin_used = rp.margin_used
                if rp.leverage >= Decimal("1"):
                    lp.leverage = max(1, int(round(float(rp.leverage))))
                lp.unrealized_pnl = rp.unrealized_pnl
                if lp.stop_loss is None and rp.stop_loss_price is not None:
                    lp.stop_loss = rp.stop_loss_price
                if lp.take_profit is None and rp.take_profit_price is not None:
                    lp.take_profit = rp.take_profit_price
                stats.updated += 1
                stats.open_positions_count += 1
                stats.total_margin_used += lp.margin_used
                stats.total_unrealized_pnl += lp.unrealized_pnl
            else:
                raw_pnl = (
                    (lp.current_price - lp.entry_price) * lp.quantity
                    if lp.side.upper() != "SHORT"
                    else (lp.entry_price - lp.current_price) * lp.quantity
                )
                lp.status = "CLOSED"
                lp.exit_reason = lp.exit_reason or "EXCHANGE_CLOSED"
                lp.closed_at = now
                lp.realized_pnl = raw_pnl - (lp.entry_fee or Decimal("0"))
                lp.unrealized_pnl = Decimal("0")
                stats.closed += 1

        for key, rp in remote_by_key.items():
            if key in matched_keys:
                continue
            expected_entry_side = "SELL" if rp.side == "SHORT" else "BUY"
            ord_stmt = (
                select(Order)
                .where(
                    Order.account_id == account.id,
                    Order.symbol == rp.symbol,
                    Order.side == expected_entry_side,
                    Order.status.in_(["FILLED", "PARTIAL"]),
                    Order.trading_mode.not_in([TradingMode.PAPER.value, TradingMode.BACKTEST.value]),
                )
                .order_by(Order.created_at.desc())
                .limit(1)
            )
            ord_res = await session.execute(ord_stmt)
            matched_order = ord_res.scalar_one_or_none()
            if (
                matched_order is not None
                and matched_order.average_fill_price is None
                and rp.entry_price > Decimal("0")
            ):
                matched_order.average_fill_price = rp.entry_price

            strategy_name = matched_order.strategy_name if matched_order else "exchange_sync"
            stop_loss = rp.stop_loss_price or (matched_order.stop_loss if matched_order else None)
            take_profit = rp.take_profit_price or (matched_order.take_profit if matched_order else None)
            opened_at = matched_order.created_at if matched_order else now
            market_regime = "UNKNOWN"
            if matched_order and matched_order.risk_decision_id is not None:
                rd = await session.get(RiskDecision, matched_order.risk_decision_id)
                if rd is not None:
                    market_regime = rd.market_regime

            sl_order_id: str | None = None
            tp_order_id: str | None = None
            try:
                open_orders = await exchange.fetch_open_orders(rp.symbol)
                for oo in open_orders:
                    otype = (oo.order_type or "").upper()
                    raw_sp = (oo.raw or {}).get("stopPrice")
                    sp_dec = Decimal(str(raw_sp)) if raw_sp not in (None, "", 0, "0") else None
                    if "STOP" in otype and "TAKE_PROFIT" not in otype:
                        sl_order_id = sl_order_id or oo.exchange_order_id or None
                        if stop_loss is None and sp_dec and sp_dec > 0:
                            stop_loss = sp_dec
                    elif "TAKE_PROFIT" in otype:
                        tp_order_id = tp_order_id or oo.exchange_order_id or None
                        if take_profit is None and sp_dec and sp_dec > 0:
                            take_profit = sp_dec
            except Exception:
                pass

            init_stop_dist: Decimal | None = None
            if stop_loss is not None and rp.entry_price > Decimal("0"):
                init_stop_dist = abs(rp.entry_price - stop_loss) / rp.entry_price

            lev_int = max(1, int(round(float(rp.leverage)))) if rp.leverage >= Decimal("1") else 1
            cur_px = rp.mark_price if rp.mark_price > Decimal("0") else rp.entry_price
            new_pos = Position(
                account_id=account.id,
                symbol=rp.symbol,
                strategy_name=strategy_name,
                trading_mode=account.trading_mode,
                market_type="FUTURES",
                leverage=lev_int,
                margin_used=rp.margin_used,
                status="OPEN",
                side=rp.side,
                quantity=rp.quantity,
                entry_price=rp.entry_price,
                current_price=cur_px,
                stop_loss=stop_loss,
                take_profit=take_profit,
                initial_stop_distance_pct=init_stop_dist,
                sl_order_id=sl_order_id,
                tp_order_id=tp_order_id,
                market_regime=market_regime,
                entry_fee=Decimal("0"),
                unrealized_pnl=rp.unrealized_pnl,
                realized_pnl=Decimal("0"),
                opened_at=opened_at,
            )
            session.add(new_pos)
            stats.imported += 1
            stats.open_positions_count += 1
            stats.total_margin_used += rp.margin_used
            stats.total_unrealized_pnl += rp.unrealized_pnl

        await session.flush()
        return stats

    async def reconcile(
        self,
        *,
        session: AsyncSession,
        account_id: uuid.UUID,
        exchange: BaseExchange,
        trading_mode: TradingMode,
    ) -> ReconciliationReport:
        repo = TradingRepository(session)
        discrepancies: list[str] = []
        orders_updated = 0

        if trading_mode in {TradingMode.PAPER, TradingMode.BACKTEST}:
            # Verify internal ledger integrity: balances >= 0
            balances = await repo.get_balances(account_id)
            for bal in balances:
                if bal.available < 0 or bal.locked < 0:
                    discrepancies.append(f"Negative balance detected for {bal.asset}: avail={bal.available}")
            await repo.log_agent_event(
                event_type="RECONCILIATION",
                component="reconciliation",
                message="Paper portfolio ledger verified.",
                severity="WARNING" if discrepancies else "INFO",
                payload={"discrepancies": discrepancies},
            )
            return ReconciliationReport(
                verified=len(discrepancies) == 0,
                trading_mode=trading_mode.value,
                discrepancies=discrepancies,
            )

        # Live / Testnet reconciliation
        try:
            remote_balances = await exchange.fetch_balances()
            local_balances = {b.asset: b for b in await repo.get_balances(account_id)}
            for asset, rb in remote_balances.items():
                lb = local_balances.get(asset)
                if lb is None or abs(lb.total - rb.total) > Decimal("0.000001") or abs(lb.locked - rb.used) > Decimal("0.000001"):
                    discrepancies.append(
                        f"Balance drift for {asset}: local={lb.total if lb else 0}, exchange={rb.total}"
                    )
                    await repo.upsert_balance(account_id, asset, rb.free, rb.used, total=rb.total)

            for asset, lb in local_balances.items():
                if asset not in remote_balances and lb.total > Decimal("0"):
                    discrepancies.append(
                        f"Balance cleared for {asset}: local={lb.total}, exchange=0"
                    )
                    await repo.upsert_balance(account_id, asset, Decimal("0"), Decimal("0"), total=Decimal("0"))

            # Reconcile live open Futures positions & reject dead unsubmitted local orders
            positions_synced = 0
            account_row = await session.get(Account, account_id)
            if account_row is not None:
                pos_stats = await self.sync_live_positions(
                    session=session,
                    account=account_row,
                    exchange=exchange,
                )
                positions_synced = pos_stats.open_positions_count
                orders_updated += pos_stats.orders_rejected
                if pos_stats.imported > 0:
                    discrepancies.append(f"Imported {pos_stats.imported} live exchange position(s) missing locally.")
                if pos_stats.closed > 0:
                    discrepancies.append(f"Closed {pos_stats.closed} stale position(s) no longer open on exchange.")

            # Reconcile only real non-simulated non-terminal orders
            stmt = select(Order).where(
                Order.account_id == account_id,
                Order.status.in_(["PENDING", "OPEN", "PARTIAL"]),
            )
            res = await session.execute(stmt)
            for order in res.scalars().all():
                is_simulated_order = (
                    (order.trading_mode or "").upper() in {TradingMode.PAPER.value, TradingMode.BACKTEST.value}
                    or (order.exchange_order_id or "").startswith("paper-")
                )
                if is_simulated_order:
                    order.status = "CANCELED"
                    orders_updated += 1
                    continue
                if order.exchange_order_id:
                    remote = await exchange.fetch_order(order.exchange_order_id, order.symbol)
                    if remote.status != order.status or remote.filled_quantity != order.filled_quantity or (
                        order.average_fill_price is None and remote.average_price is not None
                    ):
                        order.status = remote.status
                        order.filled_quantity = remote.filled_quantity
                        order.average_fill_price = remote.average_price
                        order.fee_paid = remote.fee
                        orders_updated += 1
            await session.flush()
            verified = True
        except Exception as exc:
            verified = False
            positions_synced = 0
            discrepancies.append(f"Exchange reconciliation failed: {type(exc).__name__}")

        return ReconciliationReport(
            verified=verified,
            trading_mode=trading_mode.value,
            discrepancies=discrepancies,
            orders_updated=orders_updated,
            positions_synced=positions_synced,
        )
