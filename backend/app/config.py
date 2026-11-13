"""Centralized configuration for the Survival-First Crypto Trading Agent."""

from __future__ import annotations

from decimal import Decimal
from enum import Enum
from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class TradingMode(str, Enum):
    BACKTEST = "BACKTEST"
    PAPER = "PAPER"
    TESTNET = "TESTNET"
    LIVE_READ_ONLY = "LIVE_READ_ONLY"
    LIVE_TRADING = "LIVE_TRADING"


class AutonomyMode(str, Enum):
    HYBRID = "HYBRID"
    SUPERVISED = "SUPERVISED"


class KellyMode(str, Enum):
    CONSERVATIVE = "CONSERVATIVE"
    NORMAL = "NORMAL"
    AGGRESSIVE = "AGGRESSIVE"


KELLY_MULTIPLIERS: dict[KellyMode, Decimal] = {
    KellyMode.CONSERVATIVE: Decimal("0.10"),
    KellyMode.NORMAL: Decimal("0.20"),
    KellyMode.AGGRESSIVE: Decimal("0.25"),
}


class Settings(BaseSettings):
    """Application settings loaded from environment variables and .env."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Application
    app_name: str = "Survival Trading Agent"
    app_env: Literal["development", "staging", "production", "test"] = "development"
    debug: bool = False
    log_level: str = "INFO"

    # Operating & Trading Modes
    trading_mode: TradingMode = TradingMode.PAPER
    autonomy_mode: AutonomyMode = AutonomyMode.HYBRID
    live_trading_enabled: bool = False
    live_trading_confirmed: bool = False

    # Initial Capital & Market Universe
    initial_capital_usd: Decimal = Field(default=Decimal("1.00"), gt=Decimal("0"))
    quote_currency: str = "USDT"
    market_type: Literal["FUTURES"] = "FUTURES"
    trading_universe: str = (
        "BTC/USDT,ETH/USDT,SOL/USDT,BNB/USDT,XRP/USDT,DOGE/USDT,ADA/USDT,AVAX/USDT,"
        "LINK/USDT,DOT/USDT,LTC/USDT,NEAR/USDT,APT/USDT,ARB/USDT,OP/USDT,SUI/USDT"
    )
    timeframes: str = "5m,15m,1h,4h"

    # Short-horizon scalp policy: max hold 60 min, book profit fast
    max_hold_minutes: int = Field(default=60, ge=5, le=240)
    quick_take_profit_pct: Decimal = Field(default=Decimal("0.015"), gt=Decimal("0"))
    quick_stop_loss_pct: Decimal = Field(default=Decimal("0.006"), gt=Decimal("0"))
    fast_book_profit_pct: Decimal = Field(default=Decimal("0.0045"), gt=Decimal("0"))

    # Risk & Survival Limits (Centralized)
    kelly_mode: KellyMode = KellyMode.NORMAL
    kelly_multiplier: Decimal = Field(default=Decimal("0.20"), gt=Decimal("0"), le=Decimal("0.25"))
    max_risk_per_trade: Decimal = Field(default=Decimal("0.015"), gt=Decimal("0"), le=Decimal("0.10"))
    max_daily_loss: Decimal = Field(default=Decimal("0.01"), gt=Decimal("0"), le=Decimal("0.50"))
    max_drawdown: Decimal = Field(default=Decimal("0.05"), gt=Decimal("0"), le=Decimal("0.50"))
    max_open_positions: int = Field(default=3, ge=1, le=20)
    max_leverage: int = Field(default=1, ge=1, le=1)
    min_viable_order_ratio: Decimal = Field(default=Decimal("0.01"), gt=Decimal("0"))
    max_spread_bps: Decimal = Field(default=Decimal("35.0"), gt=Decimal("0"))
    stale_data_max_seconds: int = Field(default=120, ge=5)

    # Withdrawals & Profit Management
    withdrawal_approval_required: bool = True
    min_reserve_capital_usd: Decimal = Field(default=Decimal("1.00"), ge=Decimal("0"))
    withdrawal_threshold_usd: Decimal = Field(default=Decimal("10.00"), gt=Decimal("0"))
    profit_allocation_pct: Decimal = Field(default=Decimal("0.50"), gt=Decimal("0"), le=Decimal("1.0"))

    # Database & Redis
    database_url: str = "sqlite+aiosqlite:///./survival_agent.db"
    database_sync_url: str = "sqlite:///./survival_agent.db"
    redis_url: str = "redis://localhost:6379/0"
    redis_enabled: bool = False

    # Exchange
    exchange_id: str = "binance"
    exchange_sandbox: bool = True
    exchange_api_key: SecretStr = SecretStr("")
    exchange_api_secret: SecretStr = SecretStr("")
    exchange_api_password: SecretStr = SecretStr("")

    # Security
    secret_key: SecretStr = SecretStr("CHANGE_ME_DEV_ONLY_32_BYTE_HEX_SECRET_KEY_DO_NOT_USE_IN_PROD")
    encryption_key: SecretStr = SecretStr("")
    access_token_expire_minutes: int = 60
    admin_username: str = "admin"
    admin_password: SecretStr = SecretStr("admin-dev-password-change-me")
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

    @field_validator("max_leverage")
    @classmethod
    def enforce_spot_no_leverage(cls, v: int) -> int:
        if v != 1:
            raise ValueError("Leverage > 1 is strictly prohibited in spot survival mode.")
        return v

    @field_validator("withdrawal_approval_required")
    @classmethod
    def enforce_withdrawal_approval(cls, v: bool) -> bool:
        if not v:
            raise ValueError("Human withdrawal approval cannot be disabled.")
        return v

    @property
    def symbols(self) -> list[str]:
        return [s.strip() for s in self.trading_universe.split(",") if s.strip()]

    @property
    def timeframe_list(self) -> list[str]:
        return [t.strip() for t in self.timeframes.split(",") if t.strip()]

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    def effective_kelly_multiplier(self) -> Decimal:
        # Never allow aggressive mode automatically unless explicitly configured
        mode_cap = KELLY_MULTIPLIERS[self.kelly_mode]
        return min(self.kelly_multiplier, mode_cap)

    def can_execute_live_orders(self) -> bool:
        return (
            self.trading_mode == TradingMode.LIVE_TRADING
            and self.live_trading_enabled
            and self.live_trading_confirmed
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
