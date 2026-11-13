"""Initialize database schema, default PAPER account, and strategy registry records."""

from __future__ import annotations

import asyncio
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.main import bootstrap_initial_state  # noqa: E402


async def main() -> None:
    await bootstrap_initial_state()
    print("Database initialized successfully with default PAPER account and strategies.")


if __name__ == "__main__":
    asyncio.run(main())
