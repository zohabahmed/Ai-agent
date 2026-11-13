"""Global coin discovery, symbol normalization, watchlist persistence, and multi-venue fallback."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
import re
import time
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import AgentEvent
from app.exchanges.base_exchange import BaseExchange, ExchangeError, Ticker
from app.exchanges.ccxt_adapter import CCXTExchangeAdapter
from app.market.data_collector import MarketDataCollector, MarketSnapshot
from app.market.market_cache import MarketCache

# Common quote suffixes stripped cleanly when user enters raw ticker symbols (e.g. "pepe", "PEPEUSDT", "wif-usdt")
_EXTERNAL_VENUES: tuple[tuple[str, str], ...] = (
    ("binance", "SPOT"),
    ("bybit", "FUTURES"),
    ("okx", "FUTURES"),
)

_STABLE_BASES: frozenset[str] = frozenset(
    {"USDC", "BUSD", "FDUSD", "TUSD", "USDP", "DAI", "USD1", "USDE", "EUR", "AEUR", "USTC"}
)


# Process-level short TTL caches to keep global 350+ coin scans fast (<1s after warm)
_TICKER_UNIVERSE_CACHE: dict[str, tuple[float, dict[str, Ticker]]] = {}
_MARKET_CATALOG_CACHE: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_VENUE_SYMBOL_MAP_CACHE: dict[str, tuple[float, dict[str, str]]] = {}


def normalize_pair(raw: str, default_quote: str = "USDT") -> str:
    """Normalize arbitrary user coin input (e.g. 'pepe', 'wifusdt', 'BTC-USDT') to 'BASE/USDT'."""
    cleaned = re.sub(r"[^A-Za-z0-9/:-]", "", raw or "").upper()
    cleaned = cleaned.split(":")[0]
    cleaned = cleaned.replace("-", "/")
    if "/" in cleaned:
        base, quote = cleaned.split("/", 1)
        return f"{base.strip()}/{quote.strip() or default_quote}"
    for q in (default_quote, "USDT", "USDC", "BUSD", "USD"):
        if cleaned.endswith(q) and len(cleaned) > len(q):
            return f"{cleaned[:-len(q)]}/{q}"
    return f"{cleaned}/{default_quote}" if cleaned else f"BTC/{default_quote}"


def candidate_symbol_aliases(symbol: str) -> list[str]:
    """Generate contract alias variants (e.g. PEPE/USDT -> 1000PEPE/USDT on USD-M Futures)."""
    norm = normalize_pair(symbol)
    base, quote = norm.split("/", 1)
    variants = [norm]
    if not base.startswith("1000"):
        variants.extend([f"1000{base}/{quote}", f"10000{base}/{quote}"])
    elif base.startswith("10000"):
        variants.append(f"{base[5:]}/{quote}")
    elif base.startswith("1000"):
        variants.append(f"{base[4:]}/{quote}")
    return list(dict.fromkeys(variants))


async def get_pinned_watchlist(session: AsyncSession, cache: MarketCache) -> list[str]:
    """Load user-pinned custom coins from cache or persisted DB event."""
    cached = await cache.get_json("universe:watchlist")
    if cached and isinstance(cached.get("symbols"), list):
        return [normalize_pair(str(s)) for s in cached["symbols"] if s]

    stmt = (
        select(AgentEvent)
        .where(AgentEvent.event_type == "UNIVERSE_WATCHLIST")
        .order_by(desc(AgentEvent.created_at))
        .limit(1)
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    symbols = [normalize_pair(str(s)) for s in (row.payload.get("symbols") or [])] if row and row.payload else []
    await cache.set_json("universe:watchlist", {"symbols": symbols}, ttl_seconds=600)
    return symbols


async def update_pinned_watchlist(
    session: AsyncSession,
    cache: MarketCache,
    symbol: str,
    *,
    action: str = "add",
) -> list[str]:
    """Add or remove any coin from the autonomous scanning & UI watchlist."""
    norm = normalize_pair(symbol)
    current = await get_pinned_watchlist(session, cache)
    if action.lower() == "remove":
        updated = [s for s in current if s != norm]
    else:
        updated = list(dict.fromkeys([norm, *current]))[:50]

    await cache.set_json("universe:watchlist", {"symbols": updated}, ttl_seconds=600)
    session.add(
        AgentEvent(
            event_type="UNIVERSE_WATCHLIST",
            severity="INFO",
            component="market_discovery",
            message=f"Updated global watchlist ({action}): {norm}",
            payload={"symbols": updated, "changed": norm, "action": action.lower()},
        )
    )
    await session.flush()
    return updated


class GlobalMarketDiscoveryService:
    """Discovers and collects market data for any coin across global exchanges."""

    @staticmethod
    async def fetch_global_tickers(exchange: BaseExchange, *, cache_ttl: float = 20.0) -> dict[str, Ticker]:
        ex_key = f"{getattr(exchange, 'exchange_id', 'ex')}:{getattr(exchange, 'market_type', 'FUTURES')}"
        now_mono = time.monotonic()
        cached = _TICKER_UNIVERSE_CACHE.get(ex_key)
        if cached and now_mono - cached[0] < cache_ttl and cached[1]:
            return cached[1]
        try:
            tickers = await exchange.fetch_all_tickers("USDT")
        except Exception:
            tickers = {}
        if tickers:
            _TICKER_UNIVERSE_CACHE[ex_key] = (now_mono, tickers)
        return tickers

    @staticmethod
    async def discover_hot_universe(
        exchange: BaseExchange,
        *,
        base_symbols: list[str],
        pinned_symbols: list[str],
        open_symbols: list[str],
        max_total: int = 64,
        min_quote_volume_usd: float = 2_000_000.0,
        max_buying_power_usd: float | None = None,
    ) -> list[str]:
        """
        Build the active coin scan list dynamically:
        open positions + pinned watchlist + top global volume coins + top global movers + broad midcaps.
        Prioritizes affordable markets first when max_buying_power_usd is small so <$20 wallets don't stall on BTC/ETH.
        """
        priority = list(dict.fromkeys([*open_symbols, *pinned_symbols]))
        tickers = await GlobalMarketDiscoveryService.fetch_global_tickers(exchange)
        if not tickers:
            return list(dict.fromkeys([*priority, *base_symbols]))[:max_total]

        min_notional_map: dict[str, float] = {}
        try:
            mkts = await exchange.list_tradable_markets(quote="USDT", query="", limit=600)
            for m in mkts:
                min_notional_map[m["symbol"]] = float(m.get("min_notional") or 0.0)
        except Exception:
            pass

        liquid: list[tuple[str, float, float]] = []
        fallback_active: list[tuple[str, float, float]] = []
        for sym, tk in tickers.items():
            base = sym.split("/")[0].upper()
            if base in _STABLE_BASES:
                continue
            vol = float(tk.volume_24h)
            pct = abs(float(getattr(tk, "_pct_change", 0.0)))
            if vol >= min_quote_volume_usd:
                liquid.append((sym, vol, pct))
            elif vol >= 50_000.0:
                fallback_active.append((sym, vol, pct))

        pool = liquid if len(liquid) >= max_total else [*liquid, *fallback_active]
        if not pool:
            pool = [
                (sym, float(tk.volume_24h), abs(float(getattr(tk, "_pct_change", 0.0))))
                for sym, tk in tickers.items()
                if sym.split("/")[0].upper() not in _STABLE_BASES and float(tk.last) > 0
            ]

        if max_buying_power_usd is not None and max_buying_power_usd > 0 and min_notional_map:
            affordable_pool = [
                item for item in pool
                if min_notional_map.get(item[0], 0.0) <= max_buying_power_usd
            ]
            if affordable_pool:
                pool = affordable_pool

        # Balance top global liquidity, highest volatility movers right now, and broad midcap breadth
        by_volume = [s for s, _, _ in sorted(pool, key=lambda item: item[1], reverse=True)[:22]]
        by_movers = [s for s, _, _ in sorted(pool, key=lambda item: item[2], reverse=True)[:28]]
        broad_alt = [s for s, _, _ in sorted(pool, key=lambda item: (item[2] * 0.6 + item[1] / 1e8), reverse=True)[:20]]

        combined = list(dict.fromkeys([*priority, *by_movers, *by_volume, *broad_alt, *base_symbols]))
        if max_buying_power_usd is not None and max_buying_power_usd > 0 and min_notional_map:
            affordable_first = [
                s for s in combined if s in priority or min_notional_map.get(s, 0.0) <= max_buying_power_usd
            ]
            unaffordable_tail = [s for s in combined if s not in affordable_first]
            combined = [*affordable_first, *unaffordable_tail]
        return combined[:max_total]

    @staticmethod
    async def search_global_coins(
        exchange: BaseExchange,
        *,
        query: str = "",
        limit: int = 60,
        include_external: bool = True,
    ) -> list[dict[str, Any]]:
        """Search active coins on primary USD-M Futures plus fallback global venues (Binance Spot, Bybit, OKX)."""
        q = query.strip().upper().replace("-USDT", "").replace("/USDT", "")
        tickers = await GlobalMarketDiscoveryService.fetch_global_tickers(exchange)
        primary_markets = await exchange.list_tradable_markets(quote="USDT", query=q, limit=250)

        rows: list[dict[str, Any]] = []
        seen: set[str] = set()
        for m in primary_markets:
            sym = m["symbol"]
            if sym in seen:
                continue
            seen.add(sym)
            tk = tickers.get(sym)
            rows.append(
                {
                    "symbol": sym,
                    "base": m["base"],
                    "quote": m["quote"],
                    "exchange": m.get("exchange", exchange.exchange_id),
                    "market_type": m.get("market_type", "FUTURES"),
                    "last": float(tk.last) if tk else None,
                    "change_24h_pct": float(getattr(tk, "_pct_change", 0.0)) if tk else None,
                    "volume_24h_usd": float(tk.volume_24h) if tk else 0.0,
                }
            )

        # If CCXT exchange and user searches a coin missing/sparse on Binance USD-M, query public global venues concurrently
        if include_external and isinstance(exchange, CCXTExchangeAdapter) and ((q and len(rows) < 5) or (not q and len(rows) < 20)):
            ext_rows = await GlobalMarketDiscoveryService._search_external_venues(query=q, seen=seen, limit=limit)
            rows.extend(ext_rows)

        if q:
            rows.sort(
                key=lambda r: (
                    0 if r["base"] == q or r["base"] == f"1000{q}" else 1,
                    0 if r["market_type"] == "FUTURES" else 1,
                    -(r.get("volume_24h_usd") or 0.0),
                )
            )
        else:
            rows.sort(key=lambda r: (-(r.get("volume_24h_usd") or 0.0), r["base"]))
        return rows[:max(1, limit)]

    @staticmethod
    async def _fetch_venue_catalog(ex_id: str, mtype: str, timeout: float = 4.0) -> list[dict[str, Any]]:
        now_mono = time.monotonic()
        cache_key = f"{ex_id}:{mtype}"
        cached = _MARKET_CATALOG_CACHE.get(cache_key)
        if cached:
            ttl = 300.0 if cached[1] else 45.0
            if now_mono - cached[0] < ttl:
                return cached[1]

        adapter = CCXTExchangeAdapter(exchange_id=ex_id, market_type=mtype, sandbox=False, read_only=True)
        try:
            markets = await asyncio.wait_for(
                adapter.list_tradable_markets(quote="USDT", query="", limit=600),
                timeout=timeout,
            )
            _MARKET_CATALOG_CACHE[cache_key] = (now_mono, markets)
            return markets
        except Exception:
            _MARKET_CATALOG_CACHE[cache_key] = (now_mono, [])
            return []
        finally:
            try:
                await asyncio.wait_for(adapter.close(), timeout=1.5)
            except Exception:
                pass

    @staticmethod
    async def _search_external_venues(
        *,
        query: str,
        seen: set[str],
        limit: int,
    ) -> list[dict[str, Any]]:
        catalogs = await asyncio.gather(
            *(GlobalMarketDiscoveryService._fetch_venue_catalog(ex_id, mtype) for ex_id, mtype in _EXTERNAL_VENUES),
            return_exceptions=True,
        )
        extra: list[dict[str, Any]] = []
        for (ex_id, mtype), markets in zip(_EXTERNAL_VENUES, catalogs):
            if isinstance(markets, Exception) or not markets:
                continue
            for m in markets:
                sym = m["symbol"]
                base = m["base"]
                if sym in seen:
                    continue
                if query and query not in sym.upper() and query not in base.upper():
                    continue
                seen.add(sym)
                extra.append(
                    {
                        "symbol": sym,
                        "base": base,
                        "quote": m["quote"],
                        "exchange": ex_id,
                        "market_type": mtype,
                        "last": None,
                        "change_24h_pct": None,
                        "volume_24h_usd": 0.0,
                    }
                )
                if len(extra) >= limit:
                    return extra
        return extra

    @staticmethod
    async def collect_symbol_snapshot_any(
        collector: MarketDataCollector,
        session: AsyncSession,
        symbol: str,
        *,
        timeframe: str = "15m",
        limit: int = 200,
        allow_historical: bool = True,
        allow_external_feed: bool = True,
    ) -> tuple[MarketSnapshot, str]:
        """
        Collect snapshot for ANY coin:
        1. Exact symbol on primary exchange
        2. 1000-unit perp alias on primary exchange (e.g. PEPE/USDT -> 1000PEPE/USDT)
        3. External public CCXT feed (Binance Spot / Bybit Futures / OKX Swap) in PAPER/read-only mode
        Returns (snapshot, resolved_symbol_on_primary_or_requested).
        """
        last_exc: Exception | None = None
        for alias in candidate_symbol_aliases(symbol):
            try:
                snap = await collector._collect_exact(
                    session, alias, timeframe=timeframe, limit=limit, allow_historical=allow_historical
                )
                return snap, alias
            except Exception as exc:
                last_exc = exc
                continue

        if allow_external_feed and isinstance(collector.exchange, CCXTExchangeAdapter):
            norm = normalize_pair(symbol)
            for ex_id, mtype in _EXTERNAL_VENUES:
                ext = CCXTExchangeAdapter(exchange_id=ex_id, market_type=mtype, sandbox=False, read_only=True)
                try:
                    ext_collector = MarketDataCollector(ext, cache=collector.cache, settings=collector.settings)
                    for alias in candidate_symbol_aliases(norm):
                        try:
                            rules, ticker, order_book, raw_candles = await asyncio.wait_for(
                                ext_collector.fetch_raw_bundle(alias, timeframe=timeframe, limit=limit),
                                timeout=6.0,
                            )
                            snap = await ext_collector.persist_raw_bundle(
                                session,
                                alias,
                                timeframe,
                                rules,
                                ticker,
                                order_book,
                                raw_candles,
                                allow_historical=allow_historical,
                            )
                            return snap, alias
                        except Exception:
                            continue
                finally:
                    try:
                        await asyncio.wait_for(ext.close(), timeout=1.5)
                    except Exception:
                        pass

        raise ExchangeError(f"Coin '{symbol}' was not found on active global markets.") from last_exc
