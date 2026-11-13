"""Resilient WebSocket market stream client with exponential backoff reconnection."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import websockets


class MarketWebSocketClient:
    """Connects to exchange public WebSocket streams (e.g., Binance miniTicker/trade)."""

    def __init__(
        self,
        symbols: list[str],
        on_ticker: Callable[[dict[str, Any]], Awaitable[None]],
        *,
        exchange_id: str = "binance",
        base_url: str | None = None,
    ) -> None:
        self.symbols = symbols
        self.on_ticker = on_ticker
        self.exchange_id = exchange_id.lower()
        self.base_url = base_url or self._default_url()
        self._running = False
        self.reconnect_attempts = 0
        self.last_message_at: datetime | None = None

    def _default_url(self) -> str:
        streams = "/".join(f"{s.replace('/', '').lower()}@bookTicker" for s in self.symbols)
        return f"wss://stream.binance.com:9443/stream?streams={streams}"

    async def run(self, max_reconnects: int | None = None) -> None:
        self._running = True
        backoff = 1.0
        while self._running:
            try:
                async with websockets.connect(self.base_url, ping_interval=20, ping_timeout=10) as ws:
                    backoff = 1.0
                    self.reconnect_attempts = 0
                    while self._running:
                        raw = await ws.recv()
                        self.last_message_at = datetime.now(timezone.utc)
                        parsed = self._parse_message(raw)
                        if parsed:
                            await self.on_ticker(parsed)
            except asyncio.CancelledError:
                self._running = False
                break
            except Exception:
                self.reconnect_attempts += 1
                if max_reconnects is not None and self.reconnect_attempts > max_reconnects:
                    break
                await asyncio.sleep(min(backoff, 30.0))
                backoff *= 2.0

    def stop(self) -> None:
        self._running = False

    def _parse_message(self, raw: str) -> dict[str, Any] | None:
        try:
            msg = json.loads(raw)
            data = msg.get("data", msg)
            sym_raw = data.get("s")
            if not sym_raw:
                return None
            # Normalize e.g. BTCUSDT -> BTC/USDT
            symbol = f"{sym_raw[:-4]}/{sym_raw[-4:]}" if sym_raw.endswith("USDT") else sym_raw
            bid = Decimal(str(data.get("b", 0)))
            ask = Decimal(str(data.get("a", 0)))
            if bid <= 0 or ask <= 0:
                return None
            return {
                "symbol": symbol,
                "bid": bid,
                "ask": ask,
                "last": (bid + ask) / Decimal("2"),
                "timestamp": datetime.now(timezone.utc),
            }
        except Exception:
            return None
