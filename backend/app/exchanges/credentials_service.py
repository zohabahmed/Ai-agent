"""Encrypted exchange credential management and safe live balance synchronization."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, TradingMode, get_settings
from app.database.models import Account, ExchangeCredential, Order, PortfolioSnapshot, Position
from app.database.repositories.trading_repo import TradingRepository
from app.exchanges.base_exchange import BaseExchange, ExchangeBalance, ExchangeError, ExchangePosition
from app.exchanges.ccxt_adapter import CCXTExchangeAdapter
from app.execution.reconciliation import ReconciliationService
from app.portfolio.equity_calculator import EquityCalculator
from app.risk.daily_baseline import sync_utc_day_baseline
from app.risk.drawdown_monitor import DrawdownMonitor
from app.security.secrets_manager import SecretsManager

SUPPORTED_EXCHANGES: tuple[str, ...] = (
    "binance",
    "bybit",
    "okx",
    "kraken",
    "coinbase",
    "mexc",
    "kucoinfutures",
    "gate",
)
MICRO_LIVE_FUTURES_EXCHANGES: frozenset[str] = frozenset({"mexc", "kucoinfutures", "gate"})


def min_live_order_usd(exchange_id: str | None) -> Decimal:
    """Return realistic venue-level USD order minimum (Binance/Bybit=$5, MEXC/KuCoin/Gate=$1)."""
    return Decimal("1.00") if (exchange_id or "").strip().lower() in MICRO_LIVE_FUTURES_EXCHANGES else Decimal("5.00")


@dataclass(frozen=True)
class SyncResult:
    verified: bool
    exchange_id: str
    trading_mode: str
    quote_currency: str
    available_quote: Decimal
    total_quote: Decimal
    balances: dict[str, dict[str, float]]
    error: str | None = None
    positions_synced: int = 0


class ExchangeConnectionService:
    """Stores encrypted exchange keys, builds scoped adapters, and syncs Spot balances."""

    @staticmethod
    async def get_credential(session: AsyncSession, account_id: uuid.UUID) -> ExchangeCredential | None:
        stmt = select(ExchangeCredential).where(ExchangeCredential.account_id == account_id)
        res = await session.execute(stmt)
        return res.scalar_one_or_none()

    @staticmethod
    async def save_credentials(
        session: AsyncSession,
        *,
        account: Account,
        exchange_id: str,
        api_key: str,
        api_secret: str,
        api_password: str = "",
        sandbox: bool = False,
    ) -> ExchangeCredential:
        norm_exchange = exchange_id.strip().lower()
        if norm_exchange not in SUPPORTED_EXCHANGES:
            raise ValueError(
                f"Unsupported exchange '{exchange_id}'. Supported: {', '.join(SUPPORTED_EXCHANGES)}"
            )
        clean_key = api_key.strip()
        clean_secret = api_secret.strip()
        clean_pass = api_password.strip()
        if not clean_key or not clean_secret:
            raise ValueError("Both API Key and API Secret are required.")

        secrets = SecretsManager()
        enc_key = secrets.encrypt(clean_key)
        enc_secret = secrets.encrypt(clean_secret)
        enc_pass = secrets.encrypt(clean_pass) if clean_pass else ""
        mask = SecretsManager.mask_secret(clean_key)

        cred = await ExchangeConnectionService.get_credential(session, account.id)
        if cred is None:
            cred = ExchangeCredential(
                account_id=account.id,
                exchange_id=norm_exchange,
                encrypted_api_key=enc_key,
                encrypted_api_secret=enc_secret,
                encrypted_api_password=enc_pass,
                api_key_mask=mask,
                sandbox=sandbox,
                verified=False,
                last_error=None,
            )
            session.add(cred)
        else:
            cred.exchange_id = norm_exchange
            cred.encrypted_api_key = enc_key
            cred.encrypted_api_secret = enc_secret
            cred.encrypted_api_password = enc_pass
            cred.api_key_mask = mask
            cred.sandbox = sandbox
            cred.verified = False
            cred.last_error = None

        # Always disarm live execution whenever credentials change
        account.exchange = norm_exchange
        account.is_trading_active = False
        account.live_trading_authorized = False
        await session.flush()
        return cred

    @staticmethod
    async def build_exchange_for_account(
        session: AsyncSession,
        account: Account,
        *,
        settings: Settings | None = None,
        force_read_only: bool | None = None,
    ) -> CCXTExchangeAdapter:
        cfg = settings or get_settings()
        cred = await ExchangeConnectionService.get_credential(session, account.id)

        if cred is not None:
            secrets = SecretsManager()
            exchange_id = cred.exchange_id
            api_key = secrets.decrypt(cred.encrypted_api_key)
            api_secret = secrets.decrypt(cred.encrypted_api_secret)
            api_password = secrets.decrypt(cred.encrypted_api_password) if cred.encrypted_api_password else ""
            sandbox = cred.sandbox
        else:
            exchange_id = account.exchange or cfg.exchange_id
            api_key = cfg.exchange_api_key.get_secret_value()
            api_secret = cfg.exchange_api_secret.get_secret_value()
            api_password = cfg.exchange_api_password.get_secret_value()
            sandbox = cfg.exchange_sandbox

        mode = TradingMode(account.trading_mode)
        if force_read_only is True:
            read_only = True
        elif mode == TradingMode.TESTNET:
            read_only = False
            sandbox = True
        elif mode == TradingMode.LIVE_TRADING and account.live_trading_authorized and not sandbox:
            read_only = False
        else:
            read_only = True

        return CCXTExchangeAdapter(
            exchange_id=exchange_id,
            api_key=api_key,
            api_secret=api_secret,
            api_password=api_password,
            sandbox=sandbox,
            read_only=read_only,
            market_type=account.market_type,
            leverage=account.leverage,
        )

    @staticmethod
    async def verify_and_sync_balances(
        session: AsyncSession,
        *,
        account: Account,
        exchange: BaseExchange | None = None,
        switch_to_read_only_if_paper: bool = True,
        reset_baseline_from_exchange: bool = False,
    ) -> SyncResult:
        """
        Verifies exchange connectivity/auth via read-only balance fetch and syncs Spot balances.
        Never leaves simulated paper positions/balances mixed with real exchange funds.
        """
        repo = TradingRepository(session)
        cred = await ExchangeConnectionService.get_credential(session, account.id)

        owns_adapter = exchange is None
        adapter: BaseExchange = exchange or await ExchangeConnectionService.build_exchange_for_account(
            session, account, force_read_only=True
        )
        try:
            remote_positions: list[ExchangePosition] | None = None
            try:
                remote_balances: dict[str, ExchangeBalance] = await adapter.fetch_balances()
                if (account.market_type or "").upper() == "FUTURES":
                    try:
                        remote_positions = await adapter.fetch_positions()
                    except ExchangeError:
                        remote_positions = None
            except ExchangeError as exc:
                err_msg = str(exc)
                if cred is not None:
                    cred.verified = False
                    cred.last_error = err_msg
                    await session.flush()
                return SyncResult(
                    verified=False,
                    exchange_id=adapter.exchange_id,
                    trading_mode=account.trading_mode,
                    quote_currency=account.quote_currency,
                    available_quote=Decimal("0"),
                    total_quote=Decimal("0"),
                    balances={},
                    error=err_msg,
                )

            if cred is not None:
                cred.verified = True
                cred.last_verified_at = datetime.now(timezone.utc)
                cred.last_error = None

            transitioning_from_paper = account.trading_mode in {TradingMode.PAPER.value, TradingMode.BACKTEST.value}
            if transitioning_from_paper and switch_to_read_only_if_paper:
                account.trading_mode = TradingMode.LIVE_READ_ONLY.value
                account.live_trading_authorized = False
                account.is_trading_active = False

                # Close any simulated open positions so they never mix with live holdings
                stmt = select(Position).where(
                    Position.account_id == account.id,
                    Position.status == "OPEN",
                    Position.trading_mode.in_([TradingMode.PAPER.value, TradingMode.BACKTEST.value]),
                )
                res = await session.execute(stmt)
                for pos in res.scalars().all():
                    pos.status = "CLOSED"
                    pos.closed_at = datetime.now(timezone.utc)

            # Ensure stale simulated PAPER/BACKTEST orders cannot break live exchange reconciliation
            if account.trading_mode not in {TradingMode.PAPER.value, TradingMode.BACKTEST.value}:
                stale_orders_stmt = select(Order).where(
                    Order.account_id == account.id,
                    Order.status.in_(["PENDING", "OPEN", "PARTIAL"]),
                    Order.trading_mode.in_([TradingMode.PAPER.value, TradingMode.BACKTEST.value]),
                )
                stale_res = await session.execute(stale_orders_stmt)
                for ord_row in stale_res.scalars().all():
                    ord_row.status = "CANCELED"

            # Replace local balance ledger with real exchange balances (honoring Futures walletBalance total)
            local_balances = {b.asset: b for b in await repo.get_balances(account.id)}
            for asset, lb in local_balances.items():
                if asset not in remote_balances and lb.total > Decimal("0"):
                    await repo.upsert_balance(account.id, asset, Decimal("0"), Decimal("0"), total=Decimal("0"))

            synced_map: dict[str, dict[str, float]] = {}
            for asset, rb in remote_balances.items():
                await repo.upsert_balance(account.id, asset, rb.free, rb.used, total=rb.total)
                synced_map[asset] = {
                    "available": float(rb.free),
                    "locked": float(rb.used),
                    "total": float(rb.total),
                }

            positions_synced = 0
            if account.trading_mode not in {TradingMode.PAPER.value, TradingMode.BACKTEST.value}:
                try:
                    pos_stats = await ReconciliationService.sync_live_positions(
                        session=session,
                        account=account,
                        exchange=adapter,
                        remote_positions=remote_positions,
                    )
                    positions_synced = pos_stats.open_positions_count
                except ExchangeError:
                    positions_synced = len(await repo.get_open_positions(account.id))

            quote = account.quote_currency
            quote_entry = remote_balances.get(quote)
            avail_quote = quote_entry.free if quote_entry else Decimal("0")
            locked_quote = quote_entry.used if quote_entry else Decimal("0")
            total_quote = quote_entry.total if quote_entry else Decimal("0")

            open_pos = await repo.get_open_positions(account.id)
            pos_tuples = [
                (p.quantity, p.entry_price, p.current_price, p.entry_fee, p.side, p.market_type, p.margin_used)
                for p in open_pos
            ]
            latest_snap = await repo.get_latest_snapshot(account.id)
            realized = latest_snap.realized_pnl if latest_snap else Decimal("0")
            eq = EquityCalculator.calculate_mixed(
                initial_capital=account.initial_capital,
                available_quote=avail_quote,
                locked_quote=locked_quote,
                positions=pos_tuples,
                realized_pnl=realized,
                total_fees=Decimal("0"),
            )
            true_equity = eq.total_equity if eq.total_equity > Decimal("0") else total_quote

            if reset_baseline_from_exchange or (transitioning_from_paper and switch_to_read_only_if_paper):
                baseline = true_equity if true_equity > Decimal("0") else Decimal("0.01")
                account.initial_capital = baseline
                account.day_start_equity = baseline
                account.day_start_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                account.peak_equity = baseline
            else:
                # Repair corrupted day_start_equity written while isolated margin collateral was dropped from ledger
                if account.day_start_equity <= Decimal("0") or (
                    true_equity > Decimal("0")
                    and account.day_start_equity < true_equity * Decimal("0.85")
                    and account.peak_equity >= true_equity * Decimal("0.90")
                ):
                    account.day_start_equity = true_equity
                sync_utc_day_baseline(account, true_equity)

            if true_equity > account.peak_equity:
                account.peak_equity = true_equity

            peak = max(account.peak_equity, true_equity, Decimal("0.000001"))
            drawdown_pct = max(Decimal("0"), (peak - true_equity) / peak)
            day_base = account.day_start_equity if account.day_start_equity > Decimal("0") else account.initial_capital
            daily_loss_pct = (
                max(Decimal("0"), (day_base - true_equity) / day_base) if day_base > Decimal("0") else Decimal("0")
            )
            daily_pnl_pct = (
                ((true_equity - day_base) / day_base) * Decimal("100") if day_base > Decimal("0") else Decimal("0")
            )

            settings = get_settings()
            dd_status = DrawdownMonitor(settings.max_drawdown, settings.max_daily_loss).evaluate(
                peak_equity=peak,
                current_equity=true_equity,
                day_start_equity=day_base,
            )
            if not account.emergency_stopped and account.survival_state == "HALTED" and true_equity > Decimal("0"):
                if not dd_status.drawdown_breached and not dd_status.daily_loss_breached:
                    account.survival_state = "RECOVERY" if dd_status.near_limit else "NORMAL"

            is_sim = account.trading_mode in {TradingMode.PAPER.value, TradingMode.BACKTEST.value}
            snapshot = PortfolioSnapshot(
                account_id=account.id,
                trading_mode=account.trading_mode,
                initial_capital=account.initial_capital,
                available_balance=avail_quote,
                locked_balance=locked_quote,
                open_position_value=eq.open_position_value,
                total_equity=true_equity,
                realized_pnl=eq.realized_pnl,
                unrealized_pnl=eq.unrealized_pnl,
                total_fees=Decimal("0"),
                drawdown_pct=drawdown_pct.quantize(Decimal("0.000001")),
                daily_pnl_pct=daily_pnl_pct.quantize(Decimal("0.0001")),
                withdrawable_amount=Decimal("0"),
                is_simulated=is_sim,
            )
            session.add(snapshot)
            await session.flush()

            return SyncResult(
                verified=True,
                exchange_id=adapter.exchange_id,
                trading_mode=account.trading_mode,
                quote_currency=quote,
                available_quote=avail_quote,
                total_quote=true_equity,
                balances=synced_map,
                error=None,
                positions_synced=positions_synced,
            )
        finally:
            if owns_adapter:
                await adapter.close()

    @staticmethod
    def status_payload(account: Account, cred: ExchangeCredential | None) -> dict[str, Any]:
        settings = get_settings()
        env_configured = bool(
            settings.exchange_api_key.get_secret_value() and settings.exchange_api_secret.get_secret_value()
        )
        return {
            "exchange_id": cred.exchange_id if cred else account.exchange,
            "market_type": account.market_type,
            "leverage": account.leverage,
            "margin_mode": "ISOLATED" if account.market_type == "FUTURES" else "NONE",
            "trading_mode": account.trading_mode,
            "live_trading_authorized": account.live_trading_authorized,
            "is_trading_active": account.is_trading_active,
            "sandbox": cred.sandbox if cred else settings.exchange_sandbox,
            "credentials_configured": cred is not None or env_configured,
            "api_key_mask": (
                cred.api_key_mask
                if cred
                else SecretsManager.mask_secret(settings.exchange_api_key.get_secret_value())
            ),
            "verified": cred.verified if cred else False,
            "last_verified_at": cred.last_verified_at.isoformat() if cred and cred.last_verified_at else None,
            "last_error": cred.last_error if cred else None,
            "supported_exchanges": list(SUPPORTED_EXCHANGES),
        }
