"""Create or update an admin/operator user for dashboard authentication."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.database.models import User  # noqa: E402
from app.database.repositories.trading_repo import TradingRepository  # noqa: E402
from app.database.session import get_db_manager  # noqa: E402
from app.security.authentication import hash_password  # noqa: E402


async def create_user(username: str, password: str, role: str) -> None:
    db = get_db_manager()
    await db.init_models()
    async with db.session() as session:
        repo = TradingRepository(session)
        existing = await repo.get_user_by_username(username)
        if existing:
            existing.hashed_password = hash_password(password)
            existing.role = role
            print(f"Updated user '{username}' with role '{role}'.")
        else:
            session.add(
                User(
                    username=username,
                    hashed_password=hash_password(password),
                    role=role,
                    is_active=True,
                )
            )
            print(f"Created user '{username}' with role '{role}'.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Create dashboard user.")
    parser.add_argument("--username", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--role", default="admin", choices=["admin", "operator", "viewer"])
    args = parser.parse_args()
    asyncio.run(create_user(args.username, args.password, args.role))
