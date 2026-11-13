"""SQLAlchemy ORM models for the Survival-First Crypto Trading Agent."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    """Base declarative class."""


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="viewer")  # admin, operator, viewer
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    exchange: Mapped[str] = mapped_column(String(50), nullable=False, default="binance")
    market_type: Mapped[str] = mapped_column(String(16), nullable=False, default="SPOT")
    leverage: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    trading_mode: Mapped[str] = mapped_column(String(32), nullable=False, default="PAPER")
    autonomy_mode: Mapped[str] = mapped_column(String(32), nullable=False, default="HYBRID")
    survival_state: Mapped[str] = mapped_column(String(32), nullable=False, default="NORMAL")
    initial_capital: Mapped[Decimal] = mapped_column(Numeric(24, 10), nullable=False, default=Decimal("1.00"))
    quote_currency: Mapped[str] = mapped_column(String(16), nullable=False, default="USDT")
    peak_equity: Mapped[Decimal] = mapped_column(Numeric(24, 10), nullable=False, default=Decimal("1.00"))
    is_trading_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    emergency_stopped: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    live_trading_authorized: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )

    balances: Mapped[list[Balance]] = relationship(back_populates="account", cascade="all, delete-orphan")
    orders: Mapped[list[Order]] = relationship(back_populates="account")
    positions: Mapped[list[Position]] = relationship(back_populates="account")


class Balance(Base):
    __tablename__ = "balances"
    __table_args__ = (
        UniqueConstraint("account_id", "asset", name="uq_balance_account_asset"),
        Index("ix_balances_account_asset", "account_id", "asset"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    asset: Mapped[str] = mapped_column(String(24), nullable=False)
    available: Mapped[Decimal] = mapped_column(Numeric(24, 10), nullable=False, default=Decimal("0"))
    locked: Mapped[Decimal] = mapped_column(Numeric(24, 10), nullable=False, default=Decimal("0"))
    total: Mapped[Decimal] = mapped_column(Numeric(24, 10), nullable=False, default=Decimal("0"))
    reconciled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)

    account: Mapped[Account] = relationship(back_populates="balances")


class MarketCandle(Base):
    __tablename__ = "market_candles"
    __table_args__ = (
        UniqueConstraint("exchange", "symbol", "timeframe", "timestamp", name="uq_candle_key"),
        Index("ix_candles_lookup", "exchange", "symbol", "timeframe", "timestamp"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    exchange: Mapped[str] = mapped_column(String(50), nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(16), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    open: Mapped[Decimal] = mapped_column(Numeric(24, 10), nullable=False)
    high: Mapped[Decimal] = mapped_column(Numeric(24, 10), nullable=False)
    low: Mapped[Decimal] = mapped_column(Numeric(24, 10), nullable=False)
    close: Mapped[Decimal] = mapped_column(Numeric(24, 10), nullable=False)
    volume: Mapped[Decimal] = mapped_column(Numeric(24, 10), nullable=False)
    is_validated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)


class Strategy(Base):
    __tablename__ = "strategies"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ACTIVE")  # ACTIVE, STANDBY, DISABLED, PAPER_ONLY
    is_approved_live: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    active_version_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    preferred_regimes: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    parameters: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )

    versions: Mapped[list[StrategyVersion]] = relationship(back_populates="strategy", cascade="all, delete-orphan")
    metrics: Mapped[list[StrategyMetric]] = relationship(back_populates="strategy", cascade="all, delete-orphan")


class StrategyVersion(Base):
    __tablename__ = "strategy_versions"
    __table_args__ = (
        UniqueConstraint("strategy_id", "version_number", name="uq_strategy_version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    strategy_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("strategies.id", ondelete="CASCADE"), nullable=False
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    parameters: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    change_reason: Mapped[str] = mapped_column(Text, nullable=False)
    validation_summary: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="VALIDATED")  # CANDIDATE, VALIDATED, ACTIVE, ROLLED_BACK
    created_by: Mapped[str] = mapped_column(String(64), nullable=False, default="system")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)

    strategy: Mapped[Strategy] = relationship(back_populates="versions")


class StrategyMetric(Base):
    __tablename__ = "strategy_metrics"
    __table_args__ = (
        Index("ix_strategy_metrics_lookup", "strategy_id", "window_name", "recorded_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    strategy_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("strategies.id", ondelete="CASCADE"), nullable=False
    )
    window_name: Mapped[str] = mapped_column(String(32), nullable=False, default="rolling_30")
    regime: Mapped[str] = mapped_column(String(32), nullable=False, default="ALL")
    trade_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    win_rate: Mapped[Decimal] = mapped_column(Numeric(10, 6), nullable=False, default=Decimal("0"))
    avg_win: Mapped[Decimal] = mapped_column(Numeric(24, 10), nullable=False, default=Decimal("0"))
    avg_loss: Mapped[Decimal] = mapped_column(Numeric(24, 10), nullable=False, default=Decimal("0"))
    profit_factor: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False, default=Decimal("0"))
    expected_value: Mapped[Decimal] = mapped_column(Numeric(24, 10), nullable=False, default=Decimal("0"))
    sharpe_ratio: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False, default=Decimal("0"))
    sortino_ratio: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False, default=Decimal("0"))
    max_drawdown: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False, default=Decimal("0"))
    consecutive_losses: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_fees: Mapped[Decimal] = mapped_column(Numeric(24, 10), nullable=False, default=Decimal("0"))
    avg_slippage_bps: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False, default=Decimal("0"))
    drift_detected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    drift_score: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False, default=Decimal("0"))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)

    strategy: Mapped[Strategy] = relationship(back_populates="metrics")


class TradeSignal(Base):
    __tablename__ = "trade_signals"
    __table_args__ = (
        Index("ix_trade_signals_created", "symbol", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    strategy_name: Mapped[str] = mapped_column(String(64), nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(16), nullable=False, default="15m")
    action: Mapped[str] = mapped_column(String(16), nullable=False)  # BUY, SELL, HOLD
    market_regime: Mapped[str] = mapped_column(String(32), nullable=False, default="UNKNOWN")
    confidence: Mapped[Decimal] = mapped_column(Numeric(10, 6), nullable=False, default=Decimal("0"))
    entry_price: Mapped[Decimal] = mapped_column(Numeric(24, 10), nullable=False, default=Decimal("0"))
    stop_loss: Mapped[Decimal] = mapped_column(Numeric(24, 10), nullable=False, default=Decimal("0"))
    take_profit: Mapped[Decimal] = mapped_column(Numeric(24, 10), nullable=False, default=Decimal("0"))
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    indicators_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)


class RiskDecision(Base):
    __tablename__ = "risk_decisions"
    __table_args__ = (
        Index("ix_risk_decisions_created", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    signal_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("trade_signals.id", ondelete="SET NULL"), nullable=True
    )
    account_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True
    )
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    strategy_name: Mapped[str] = mapped_column(String(64), nullable=False)
    market_regime: Mapped[str] = mapped_column(String(32), nullable=False, default="UNKNOWN")
    survival_state: Mapped[str] = mapped_column(String(32), nullable=False)
    decision: Mapped[str] = mapped_column(String(24), nullable=False)  # APPROVED, REJECTED, PENDING
    win_probability: Mapped[Decimal] = mapped_column(Numeric(10, 6), nullable=False, default=Decimal("0"))
    probability_lower_bound: Mapped[Decimal] = mapped_column(Numeric(10, 6), nullable=False, default=Decimal("0"))
    expected_value: Mapped[Decimal] = mapped_column(Numeric(24, 10), nullable=False, default=Decimal("0"))
    full_kelly: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False, default=Decimal("0"))
    fractional_kelly: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False, default=Decimal("0"))
    approved_notional: Mapped[Decimal] = mapped_column(Numeric(24, 10), nullable=False, default=Decimal("0"))
    approved_quantity: Mapped[Decimal] = mapped_column(Numeric(24, 10), nullable=False, default=Decimal("0"))
    estimated_fees: Mapped[Decimal] = mapped_column(Numeric(24, 10), nullable=False, default=Decimal("0"))
    rejection_reasons: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    decision_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        UniqueConstraint("client_order_id", name="uq_orders_client_order_id"),
        Index("ix_orders_account_status", "account_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    risk_decision_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("risk_decisions.id", ondelete="SET NULL"), nullable=True
    )
    client_order_id: Mapped[str] = mapped_column(String(64), nullable=False)
    exchange_order_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    exchange: Mapped[str] = mapped_column(String(50), nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    side: Mapped[str] = mapped_column(String(16), nullable=False)  # BUY, SELL
    order_type: Mapped[str] = mapped_column(String(16), nullable=False, default="MARKET")  # MARKET, LIMIT
    trading_mode: Mapped[str] = mapped_column(String(32), nullable=False, default="PAPER")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")  # PENDING, OPEN, PARTIAL, FILLED, CANCELED, REJECTED
    requested_quantity: Mapped[Decimal] = mapped_column(Numeric(24, 10), nullable=False)
    filled_quantity: Mapped[Decimal] = mapped_column(Numeric(24, 10), nullable=False, default=Decimal("0"))
    limit_price: Mapped[Decimal | None] = mapped_column(Numeric(24, 10), nullable=True)
    average_fill_price: Mapped[Decimal | None] = mapped_column(Numeric(24, 10), nullable=True)
    fee_paid: Mapped[Decimal] = mapped_column(Numeric(24, 10), nullable=False, default=Decimal("0"))
    fee_asset: Mapped[str] = mapped_column(String(16), nullable=False, default="USDT")
    strategy_name: Mapped[str] = mapped_column(String(64), nullable=False, default="manual")
    stop_loss: Mapped[Decimal | None] = mapped_column(Numeric(24, 10), nullable=True)
    take_profit: Mapped[Decimal | None] = mapped_column(Numeric(24, 10), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )

    account: Mapped[Account] = relationship(back_populates="orders")


class Trade(Base):
    __tablename__ = "trades"
    __table_args__ = (
        UniqueConstraint("exchange", "exchange_trade_id", name="uq_trade_exchange_exec"),
        Index("ix_trades_account_executed", "account_id", "executed_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    order_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("orders.id", ondelete="CASCADE"), nullable=False
    )
    exchange: Mapped[str] = mapped_column(String(50), nullable=False)
    exchange_trade_id: Mapped[str] = mapped_column(String(128), nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    side: Mapped[str] = mapped_column(String(16), nullable=False)
    trading_mode: Mapped[str] = mapped_column(String(32), nullable=False, default="PAPER")
    strategy_name: Mapped[str] = mapped_column(String(64), nullable=False)
    market_regime: Mapped[str] = mapped_column(String(32), nullable=False, default="UNKNOWN")
    quantity: Mapped[Decimal] = mapped_column(Numeric(24, 10), nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(24, 10), nullable=False)
    notional: Mapped[Decimal] = mapped_column(Numeric(24, 10), nullable=False)
    fee: Mapped[Decimal] = mapped_column(Numeric(24, 10), nullable=False, default=Decimal("0"))
    slippage_bps: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False, default=Decimal("0"))
    realized_pnl: Mapped[Decimal | None] = mapped_column(Numeric(24, 10), nullable=True)
    is_simulated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    executed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)


class Position(Base):
    __tablename__ = "positions"
    __table_args__ = (
        Index("ix_positions_account_status", "account_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    strategy_name: Mapped[str] = mapped_column(String(64), nullable=False)
    trading_mode: Mapped[str] = mapped_column(String(32), nullable=False, default="PAPER")
    market_type: Mapped[str] = mapped_column(String(16), nullable=False, default="SPOT")
    leverage: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    margin_used: Mapped[Decimal] = mapped_column(Numeric(24, 10), nullable=False, default=Decimal("0"))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="OPEN")  # OPEN, CLOSED
    side: Mapped[str] = mapped_column(String(16), nullable=False, default="LONG")
    quantity: Mapped[Decimal] = mapped_column(Numeric(24, 10), nullable=False)
    entry_price: Mapped[Decimal] = mapped_column(Numeric(24, 10), nullable=False)
    current_price: Mapped[Decimal] = mapped_column(Numeric(24, 10), nullable=False)
    stop_loss: Mapped[Decimal | None] = mapped_column(Numeric(24, 10), nullable=True)
    take_profit: Mapped[Decimal | None] = mapped_column(Numeric(24, 10), nullable=True)
    entry_fee: Mapped[Decimal] = mapped_column(Numeric(24, 10), nullable=False, default=Decimal("0"))
    unrealized_pnl: Mapped[Decimal] = mapped_column(Numeric(24, 10), nullable=False, default=Decimal("0"))
    realized_pnl: Mapped[Decimal] = mapped_column(Numeric(24, 10), nullable=False, default=Decimal("0"))
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    account: Mapped[Account] = relationship(back_populates="positions")


class PortfolioSnapshot(Base):
    __tablename__ = "portfolio_snapshots"
    __table_args__ = (
        Index("ix_portfolio_snapshots_account_ts", "account_id", "recorded_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    trading_mode: Mapped[str] = mapped_column(String(32), nullable=False, default="PAPER")
    initial_capital: Mapped[Decimal] = mapped_column(Numeric(24, 10), nullable=False)
    available_balance: Mapped[Decimal] = mapped_column(Numeric(24, 10), nullable=False)
    locked_balance: Mapped[Decimal] = mapped_column(Numeric(24, 10), nullable=False, default=Decimal("0"))
    open_position_value: Mapped[Decimal] = mapped_column(Numeric(24, 10), nullable=False, default=Decimal("0"))
    total_equity: Mapped[Decimal] = mapped_column(Numeric(24, 10), nullable=False)
    realized_pnl: Mapped[Decimal] = mapped_column(Numeric(24, 10), nullable=False, default=Decimal("0"))
    unrealized_pnl: Mapped[Decimal] = mapped_column(Numeric(24, 10), nullable=False, default=Decimal("0"))
    total_fees: Mapped[Decimal] = mapped_column(Numeric(24, 10), nullable=False, default=Decimal("0"))
    drawdown_pct: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False, default=Decimal("0"))
    daily_pnl_pct: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False, default=Decimal("0"))
    withdrawable_amount: Mapped[Decimal] = mapped_column(Numeric(24, 10), nullable=False, default=Decimal("0"))
    is_simulated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)


class AgentEvent(Base):
    __tablename__ = "agent_events"
    __table_args__ = (
        Index("ix_agent_events_type_ts", "event_type", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False, default="INFO")  # INFO, WARNING, CRITICAL
    component: Mapped[str] = mapped_column(String(64), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)


class Approval(Base):
    __tablename__ = "approvals"
    __table_args__ = (
        Index("ix_approvals_status_created", "status", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    action_type: Mapped[str] = mapped_column(String(64), nullable=False)  # WITHDRAWAL, LIVE_TRADING, RISK_LIMIT_CHANGE, NEW_STRATEGY, DESTINATION_ADDRESS
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="PENDING")  # PENDING, APPROVED, REJECTED, EXPIRED
    requested_by: Mapped[str] = mapped_column(String(64), nullable=False, default="agent")
    reviewed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    review_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DestinationAddress(Base):
    __tablename__ = "destination_addresses"
    __table_args__ = (
        UniqueConstraint("address", "network", name="uq_dest_address_network"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    label: Mapped[str] = mapped_column(String(100), nullable=False)
    asset: Mapped[str] = mapped_column(String(24), nullable=False, default="USDT")
    network: Mapped[str] = mapped_column(String(32), nullable=False)
    address: Mapped[str] = mapped_column(String(255), nullable=False)
    destination_type: Mapped[str] = mapped_column(String(32), nullable=False, default="WALLET")  # WALLET, EXCHANGE
    is_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)


class WithdrawalRequest(Base):
    __tablename__ = "withdrawal_requests"
    __table_args__ = (
        Index("ix_withdrawals_status_created", "status", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    approval_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("approvals.id", ondelete="SET NULL"), nullable=True
    )
    destination_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("destination_addresses.id", ondelete="SET NULL"), nullable=True
    )
    asset: Mapped[str] = mapped_column(String(24), nullable=False, default="USDT")
    network: Mapped[str] = mapped_column(String(32), nullable=False)
    destination_address: Mapped[str] = mapped_column(String(255), nullable=False)
    requested_amount: Mapped[Decimal] = mapped_column(Numeric(24, 10), nullable=False)
    network_fee: Mapped[Decimal] = mapped_column(Numeric(24, 10), nullable=False, default=Decimal("0"))
    net_amount: Mapped[Decimal] = mapped_column(Numeric(24, 10), nullable=False)
    realized_profit_at_request: Mapped[Decimal] = mapped_column(Numeric(24, 10), nullable=False)
    reserve_capital_preserved: Mapped[Decimal] = mapped_column(Numeric(24, 10), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING_APPROVAL")  # PENDING_APPROVAL, APPROVED, EXECUTED, REJECTED, FAILED
    tx_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuditLog(Base):
    """Append-only tamper-evident audit trail using SHA-256 hash chaining."""

    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_logs_created", "created_at"),
        Index("ix_audit_logs_action", "action"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    actor: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    previous_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    record_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)


class ExchangeCredential(Base):
    """Encrypted exchange API credentials scoped to a trading account."""

    __tablename__ = "exchange_credentials"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("accounts.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    exchange_id: Mapped[str] = mapped_column(String(50), nullable=False, default="binance")
    encrypted_api_key: Mapped[str] = mapped_column(Text, nullable=False)
    encrypted_api_secret: Mapped[str] = mapped_column(Text, nullable=False)
    encrypted_api_password: Mapped[str] = mapped_column(Text, nullable=False, default="")
    api_key_mask: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    sandbox: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )
