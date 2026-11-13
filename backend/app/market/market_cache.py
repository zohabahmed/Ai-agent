"""Redis-backed market cache with automatic in-memory fallback."""

from __future__ import annotations

import json
import time
from typing import Any

import redis.asyncio as aioredis

from app.config import get_settings


class MarketCache:
    """Caches tickers, order books, market rules, and system heartbeats."""

    def __init__(self, redis_url: str | None = None, enabled: bool | None = None) -> None:
        settings = get_settings()
        self._redis_url = redis_url or settings.redis_url
        self._enabled = settings.redis_enabled if enabled is None else enabled
        self._client: aioredis.Redis | None = None
        self._memory: dict[str, tuple[float | None, str]] = {}

    async def _get_redis(self) -> aioredis.Redis | None:
        if not self._enabled:
            return None
        if self._client is None:
            try:
                self._client = aioredis.from_url(self._redis_url, decode_responses=True)
                await self._client.ping()
            except Exception:
                self._client = None
                return None
        return self._client

    async def set_json(self, key: str, value: dict[str, Any], ttl_seconds: int | None = 120) -> None:
        serialized = json.dumps(value, default=str)
        client = await self._get_redis()
        if client is not None:
            try:
                if ttl_seconds:
                    await client.setex(key, ttl_seconds, serialized)
                else:
                    await client.set(key, serialized)
                return
            except Exception:
                pass
        expires_at = (time.monotonic() + ttl_seconds) if ttl_seconds else None
        self._memory[key] = (expires_at, serialized)

    async def get_json(self, key: str) -> dict[str, Any] | None:
        client = await self._get_redis()
        if client is not None:
            try:
                raw = await client.get(key)
                if raw:
                    return json.loads(raw)
            except Exception:
                pass
        entry = self._memory.get(key)
        if not entry:
            return None
        expires_at, serialized = entry
        if expires_at is not None and time.monotonic() > expires_at:
            self._memory.pop(key, None)
            return None
        return json.loads(serialized)

    async def health_check(self) -> dict[str, Any]:
        if not self._enabled:
            return {"mode": "in_memory_fallback", "healthy": True}
        client = await self._get_redis()
        if client is None:
            return {"mode": "redis", "healthy": False}
        try:
            await client.ping()
            return {"mode": "redis", "healthy": True}
        except Exception as exc:
            return {"mode": "redis", "healthy": False, "error": str(exc)}

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
