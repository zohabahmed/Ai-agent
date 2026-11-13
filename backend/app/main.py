"""FastAPI application entrypoint for Survival-First Autonomous Crypto Trading Agent."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select

from app.api.dependencies import (
    get_market_cache,
    get_strategy_registry,
    get_survival_controller,
)
from app.api.routes import (
    approvals,
    dashboard,
    exchange,
    market,
    portfolio,
    risk,
    strategies,
    trading,
    withdrawals,
)
from app.config import get_settings
from app.database.models import Strategy, User
from app.database.repositories.trading_repo import TradingRepository
from app.database.session import get_db_manager
from app.security.authentication import hash_password
from app.strategies.strategy_registry import StrategyRegistry
from app.workers.trading_worker import AutonomousTradingLoop

logger = logging.getLogger(__name__)


async def bootstrap_initial_state() -> None:
    """Ensure initial admin user, default PAPER account, and strategy rows exist."""
    settings = get_settings()
    db = get_db_manager()
    await db.init_models()
    async with db.session() as session:
        repo = TradingRepository(session)
        await repo.get_or_create_default_account(
            exchange=settings.exchange_id,
            trading_mode=settings.trading_mode.value,
            initial_capital=settings.initial_capital_usd,
            quote_currency=settings.quote_currency,
        )

        # Ensure admin user exists
        existing_admin = await repo.get_user_by_username(settings.admin_username)
        if existing_admin is None:
            session.add(
                User(
                    username=settings.admin_username,
                    hashed_password=hash_password(settings.admin_password.get_secret_value()),
                    role="admin",
                    is_active=True,
                )
            )

        # Ensure built-in strategies are registered in DB
        registry = StrategyRegistry()
        for strat in registry.list_all():
            res = await session.execute(select(Strategy).where(Strategy.name == strat.name))
            if res.scalar_one_or_none() is None:
                session.add(
                    Strategy(
                        name=strat.name,
                        display_name=strat.display_name,
                        category=strat.category,
                        status="ACTIVE",
                        is_approved_live=False,
                        preferred_regimes=strat.preferred_regimes,
                        parameters=strat.params,
                    )
                )
        await session.flush()


async def _background_trading_daemon() -> None:
    """Continuously run the 22-step autonomous trading cycle in the background when active."""
    await asyncio.sleep(5)
    db = get_db_manager()
    while True:
        loop = AutonomousTradingLoop(
            registry=get_strategy_registry(),
            survival=get_survival_controller(),
            cache=get_market_cache(),
        )
        try:
            async with db.session() as session:
                await loop.run_cycle(session, timeframe="15m")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Background trading cycle error: %s", exc)
        finally:
            with suppress(Exception):
                await loop.exchange.close()
        await asyncio.sleep(15)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await bootstrap_initial_state()
    bg_task = asyncio.create_task(_background_trading_daemon())
    try:
        yield
    finally:
        bg_task.cancel()
        with suppress(asyncio.CancelledError):
            await bg_task
        await get_db_manager().dispose()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description="Survival-first autonomous cryptocurrency trading agent API.",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(dashboard.router, prefix="/api")
    app.include_router(exchange.router, prefix="/api")
    app.include_router(portfolio.router, prefix="/api")
    app.include_router(market.router, prefix="/api")
    app.include_router(strategies.router, prefix="/api")
    app.include_router(trading.router, prefix="/api")
    app.include_router(risk.router, prefix="/api")
    app.include_router(approvals.router, prefix="/api")
    app.include_router(withdrawals.router, prefix="/api")
    return app


app = create_app()
