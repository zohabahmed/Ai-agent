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

    @staticmethod
    def _ensure_sqlite_columns(sync_conn: Any) -> None:
        if sync_conn.dialect.name != "sqlite":
            return
        from sqlalchemy import text

        migrations: dict[str, list[tuple[str, str]]] = {
            "accounts": [
                ("day_start_equity", "NUMERIC(24, 10) NOT NULL DEFAULT 1.0"),
                ("day_start_date", "VARCHAR(16)"),
            ],
            "positions": [
                ("initial_stop_distance_pct", "NUMERIC(12, 6)"),
                ("atr_at_entry", "NUMERIC(24, 10)"),
                ("sl_order_id", "VARCHAR(128)"),
                ("tp_order_id", "VARCHAR(128)"),
                ("scaled_out", "BOOLEAN NOT NULL DEFAULT 0"),
                ("exit_reason", "VARCHAR(64)"),
                ("net_pnl_pct", "NUMERIC(14, 8)"),
                ("market_regime", "VARCHAR(48)"),
            ],
        }
        for table, cols in migrations.items():
            info_rows = sync_conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
            existing = {str(r[1]) for r in info_rows}
            for col_name, col_ddl in cols:
                if col_name not in existing:
                    sync_conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_ddl}"))
        # Backfill day_start_equity on legacy rows still at default 1.0 when initial_capital differs
        sync_conn.execute(
            text(
                "UPDATE accounts SET day_start_equity = initial_capital "
                "WHERE day_start_date IS NULL"
            )
        )

    async def init_models(self) -> None:
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await conn.run_sync(self._ensure_sqlite_columns)

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
