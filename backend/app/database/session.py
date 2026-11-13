"""Database engine and async session management."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import get_settings
from app.database.models import Base


def create_engine_from_url(database_url: str, echo: bool = False) -> AsyncEngine:
    kwargs: dict = {"echo": echo, "future": True}
    is_sqlite = database_url.startswith("sqlite")
    if is_sqlite:
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
    else:
        kwargs["pool_pre_ping"] = True
        kwargs["pool_size"] = 10
        kwargs["max_overflow"] = 20
    engine = create_async_engine(database_url, **kwargs)
    if is_sqlite:
        is_memory = ":memory:" in database_url

        @event.listens_for(engine.sync_engine, "connect")
        def _set_sqlite_pragmas(dbapi_connection, _connection_record) -> None:
            cursor = dbapi_connection.cursor()
            if not is_memory:
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.close()

    return engine


class DatabaseManager:
    """Manages SQLAlchemy async engine and session factory."""

    def __init__(self, database_url: str | None = None, echo: bool = False) -> None:
        settings = get_settings()
        self.database_url = database_url or settings.database_url
        self.engine = create_engine_from_url(self.database_url, echo=echo)
        self.session_factory = async_sessionmaker(
            bind=self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )

    async def init_models(self) -> None:
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def drop_models(self) -> None:
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)

    async def health_check(self) -> bool:
        try:
            async with self.session_factory() as session:
                await session.execute(text("SELECT 1"))
            return True
        except Exception:
            return False

    @asynccontextmanager
    async def session(self) -> AsyncGenerator[AsyncSession, None]:
        async with self.session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def dispose(self) -> None:
        await self.engine.dispose()


_db_manager: DatabaseManager | None = None


def get_db_manager() -> DatabaseManager:
    global _db_manager
    if _db_manager is None:
        _db_manager = DatabaseManager()
    return _db_manager


def reset_db_manager(database_url: str | None = None) -> DatabaseManager:
    global _db_manager
    _db_manager = DatabaseManager(database_url=database_url)
    return _db_manager


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    manager = get_db_manager()
    async with manager.session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
