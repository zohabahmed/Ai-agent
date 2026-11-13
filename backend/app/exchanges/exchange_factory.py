"""Exchange factory enforcing trading mode & credential safety."""

from __future__ import annotations

from app.config import Settings, TradingMode, get_settings
from app.exchanges.base_exchange import BaseExchange
from app.exchanges.ccxt_adapter import CCXTExchangeAdapter


class ExchangeFactory:
    """Constructs exchange adapters respecting PAPER/TESTNET/LIVE safety boundaries."""

    @staticmethod
    def create(settings: Settings | None = None) -> BaseExchange:
        cfg = settings or get_settings()
        read_only = not cfg.can_execute_live_orders()
        if cfg.trading_mode == TradingMode.TESTNET:
            read_only = False
            sandbox = True
        elif cfg.trading_mode == TradingMode.LIVE_TRADING and cfg.can_execute_live_orders():
            read_only = False
            sandbox = False
        else:
            # PAPER, BACKTEST, LIVE_READ_ONLY never send live exchange orders
            read_only = True
            sandbox = cfg.exchange_sandbox

        return CCXTExchangeAdapter(
            exchange_id=cfg.exchange_id,
            api_key=cfg.exchange_api_key.get_secret_value(),
            api_secret=cfg.exchange_api_secret.get_secret_value(),
            api_password=cfg.exchange_api_password.get_secret_value(),
            sandbox=sandbox,
            read_only=read_only,
        )
