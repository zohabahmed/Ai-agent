"""CCXT async spot exchange adapter with rate limiting, precision handling, and credential safety."""

from __future__ import annotations

import asyncio
import re
import time
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN
from typing import Any

import ccxt.async_support as ccxt_async

from app.exchanges.base_exchange import (
    BaseExchange,
    ExchangeBalance,
    ExchangeError,
    MarketRules,
    OrderBook,
    OrderBookLevel,
    OrderRequest,
    OrderResult,
    Ticker,
)


def _decimal_places(step_or_prec: Any, default: int = 8) -> int:
    if step_or_prec is None:
        return default
    if isinstance(step_or_prec, int):
        return max(0, step_or_prec)
    try:
        d = Decimal(str(step_or_prec))
        if d <= 0:
            return default
        if d >= 1:
            return 0
        exp = d.normalize().as_tuple().exponent
        return abs(int(exp)) if isinstance(exp, int) else default
    except Exception:
        return default


def _safe_exc_summary(exc: Exception) -> str:
    msg = re.sub(r"[A-Za-z0-9]{24,}", "***", str(exc) or "")
    msg = " ".join(msg.split())[:180]
    return f"{type(exc).__name__}: {msg}" if msg else type(exc).__name__


def round_step_down(value: Decimal, decimals: int) -> Decimal:
    quant = Decimal("1").scaleb(-decimals)
    return value.quantize(quant, rounding=ROUND_DOWN)


class CCXTExchangeAdapter(BaseExchange):
    """CCXT exchange adapter supporting Spot and isolated USD-M Perpetual Futures."""

    def __init__(
        self,
        exchange_id: str = "binance",
        *,
        api_key: str = "",
        api_secret: str = "",
        api_password: str = "",
        sandbox: bool = True,
        read_only: bool = True,
        market_type: str = "SPOT",
        leverage: int = 1,
    ) -> None:
        self.exchange_id = exchange_id.lower()
        self.market_type = market_type.upper()
        self.leverage = max(1, min(int(leverage), 5)) if self.market_type == "FUTURES" else 1
        self._api_key = api_key
        self._api_secret = api_secret
        self._api_password = api_password
        self._sandbox = sandbox
        self._read_only = read_only
        self._client: ccxt_async.Exchange | None = None
        self._markets_loaded = False
        self._lock = asyncio.Lock()

    def _build_client(self) -> ccxt_async.Exchange:
        ccxt_id = (
            "binanceusdm"
            if self.market_type == "FUTURES" and self.exchange_id == "binance"
            else self.exchange_id
        )
        exchange_cls = getattr(ccxt_async, ccxt_id, None)
        if exchange_cls is None:
            raise ExchangeError(f"Unsupported CCXT exchange: {ccxt_id}")

        default_type = "future" if self.market_type == "FUTURES" and self.exchange_id == "binance" else (
            "swap" if self.market_type == "FUTURES" else "spot"
        )
        params: dict[str, Any] = {
            "enableRateLimit": True,
            "options": {"defaultType": default_type},
        }
        if self._api_key and self._api_secret:
            params["apiKey"] = self._api_key
            params["secret"] = self._api_secret
            if self._api_password:
                params["password"] = self._api_password

        client = exchange_cls(params)
        if self._sandbox:
            try:
                client.set_sandbox_mode(True)
            except Exception:
                # Not every exchange implements sandbox mode
                pass
        return client

    async def _ensure_connected(self) -> ccxt_async.Exchange:
        async with self._lock:
            if self._client is None:
                self._client = self._build_client()
            if not self._markets_loaded:
                try:
                    await self._client.load_markets()
                    self._markets_loaded = True
                except Exception as exc:
                    raise ExchangeError(f"Failed to load markets for {self.exchange_id}: {type(exc).__name__}") from exc
            return self._client

    async def connect(self) -> None:
        await self._ensure_connected()

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None
            self._markets_loaded = False

    async def ping(self) -> float:
        client = await self._ensure_connected()
        t0 = time.perf_counter()
        try:
            await client.fetch_time()
        except Exception:
            # Fallback to fetching a lightweight ticker if fetch_time is unsupported
            await client.fetch_ticker("BTC/USDT")
        return (time.perf_counter() - t0) * 1000.0

    def _resolve_symbol(self, client: ccxt_async.Exchange, symbol: str) -> str:
        markets = getattr(client, "markets", None) or {}
        if symbol in markets:
            return symbol
        perp_candidate = f"{symbol}:USDT"
        if self.market_type == "FUTURES" and perp_candidate in markets:
            return perp_candidate
        return symbol

    async def configure_futures_position(
        self, symbol: str, leverage: int, margin_mode: str = "isolated"
    ) -> None:
        if self.market_type != "FUTURES" or self._read_only:
            return None
        client = await self._ensure_connected()
        resolved = self._resolve_symbol(client, symbol)
        safe_lev = max(1, min(int(leverage), 5))
        try:
            await client.set_margin_mode(margin_mode, resolved)
        except Exception:
            # Exchange may report "No need to change margin type" if already isolated
            pass
        try:
            await client.set_leverage(safe_lev, resolved)
        except Exception as exc:
            raise ExchangeError(
                f"Failed to set Futures leverage on {self.exchange_id}: {_safe_exc_summary(exc)}"
            ) from exc

    @staticmethod
    def _extract_min_notional(exchange_id: str, market: dict[str, Any]) -> Decimal:
        """Read true venue min notional without inventing a fake $5 floor on micro-contract exchanges."""
        limits = market.get("limits") or {}
        cost_min = (limits.get("cost") or {}).get("min")
        if cost_min is None:
            info = market.get("info") or {}
            if isinstance(info, dict):
                lot_filter = info.get("lotSizeFilter")
                if isinstance(lot_filter, dict) and lot_filter.get("minNotionalValue") is not None:
                    cost_min = lot_filter.get("minNotionalValue")
                elif info.get("minNotional") is not None:
                    cost_min = info.get("minNotional")
        if cost_min is None:
            cost_min = 5.0 if exchange_id.lower() in {"binance", "bybit", "bitget"} else 0.0
        return Decimal(str(cost_min))

    async def get_market_rules(self, symbol: str) -> MarketRules:
        client = await self._ensure_connected()
        resolved = self._resolve_symbol(client, symbol)
        market = client.market(resolved)
        limits = market.get("limits") or {}
        precision = market.get("precision") or {}
        c_size = Decimal(str(market.get("contractSize") or 1))
        raw_amount_min = Decimal(str((limits.get("amount") or {}).get("min") or 0.00001))
        raw_qty_prec = _decimal_places(precision.get("amount"), 6)

        if c_size > Decimal("0") and c_size != Decimal("1"):
            contract_step = Decimal("1").scaleb(-raw_qty_prec)
            lot_step: Decimal | None = contract_step * c_size
            min_quantity = raw_amount_min * c_size
            qty_prec = max(0, _decimal_places(lot_step, 6))
        else:
            lot_step = None
            min_quantity = raw_amount_min
            qty_prec = raw_qty_prec

        cost_min = self._extract_min_notional(self.exchange_id, market)
        price_prec = _decimal_places(precision.get("price"), 2)
        maker = Decimal(str(market.get("maker") if market.get("maker") is not None else "0.001"))
        taker = Decimal(str(market.get("taker") if market.get("taker") is not None else "0.001"))
        return MarketRules(
            symbol=symbol,
            base_asset=market.get("base", symbol.split("/")[0]),
            quote_asset=market.get("quote", symbol.split("/")[1]),
            min_quantity=min_quantity,
            min_notional=cost_min,
            quantity_precision=qty_prec,
            price_precision=price_prec,
            maker_fee=maker,
            taker_fee=taker,
            is_active=bool(market.get("active", True)),
            contract_size=c_size,
            lot_step=lot_step,
        )

    async def fetch_ohlcv(
        self, symbol: str, timeframe: str, since: datetime | None = None, limit: int = 200
    ) -> list[dict[str, Any]]:
        client = await self._ensure_connected()
        resolved = self._resolve_symbol(client, symbol)
        since_ms = int(since.timestamp() * 1000) if since else None
        try:
            raw = await client.fetch_ohlcv(resolved, timeframe=timeframe, since=since_ms, limit=limit)
        except Exception as exc:
            raise ExchangeError(f"OHLCV fetch failed for {symbol} {timeframe}: {type(exc).__name__}") from exc

        candles: list[dict[str, Any]] = []
        for row in raw:
            ts_ms, o, h, l, c, v = row[:6]
            candles.append(
                {
                    "timestamp": datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc),
                    "open": Decimal(str(o)),
                    "high": Decimal(str(h)),
                    "low": Decimal(str(l)),
                    "close": Decimal(str(c)),
                    "volume": Decimal(str(v)),
                }
            )
        return candles

    async def fetch_ticker(self, symbol: str) -> Ticker:
        client = await self._ensure_connected()
        resolved = self._resolve_symbol(client, symbol)
        try:
            t = await client.fetch_ticker(resolved)
        except Exception as exc:
            raise ExchangeError(f"Ticker fetch failed for {symbol}: {type(exc).__name__}") from exc
        ts_ms = t.get("timestamp")
        ts = (
            datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc)
            if ts_ms
            else datetime.now(timezone.utc)
        )
        last = Decimal(str(t.get("last") or t.get("close") or 0))
        bid = Decimal(str(t.get("bid") or last))
        ask = Decimal(str(t.get("ask") or last))
        vol = Decimal(str(t.get("quoteVolume") or t.get("baseVolume") or 0))
        return Ticker(symbol=symbol, last=last, bid=bid, ask=ask, volume_24h=vol, timestamp=ts)

    async def fetch_order_book(self, symbol: str, limit: int = 20) -> OrderBook:
        client = await self._ensure_connected()
        resolved = self._resolve_symbol(client, symbol)
        try:
            ob = await client.fetch_order_book(resolved, limit=limit)
        except Exception as exc:
            raise ExchangeError(f"Order book fetch failed for {symbol}: {type(exc).__name__}") from exc
        ts_ms = ob.get("timestamp")
        ts = (
            datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc)
            if ts_ms
            else datetime.now(timezone.utc)
        )
        bids = [OrderBookLevel(price=Decimal(str(p)), quantity=Decimal(str(q))) for p, q in ob.get("bids", [])]
        asks = [OrderBookLevel(price=Decimal(str(p)), quantity=Decimal(str(q))) for p, q in ob.get("asks", [])]
        return OrderBook(symbol=symbol, bids=bids, asks=asks, timestamp=ts)

    async def fetch_balances(self) -> dict[str, ExchangeBalance]:
        if not self._api_key or not self._api_secret:
            raise ExchangeError("Exchange API credentials are not configured.")
        client = await self._ensure_connected()
        try:
            raw = await client.fetch_balance()
        except Exception as exc:
            raise ExchangeError(f"Balance fetch failed: {type(exc).__name__}") from exc

        balances: dict[str, ExchangeBalance] = {}
        total_map = raw.get("total", {}) or {}
        free_map = raw.get("free", {}) or {}
        used_map = raw.get("used", {}) or {}
        for asset, tot in total_map.items():
            t_dec = Decimal(str(tot or 0))
            f_dec = Decimal(str(free_map.get(asset, 0) or 0))
            u_dec = Decimal(str(used_map.get(asset, 0) or 0))
            if t_dec > 0 or f_dec > 0 or u_dec > 0:
                balances[asset] = ExchangeBalance(asset=asset, free=f_dec, used=u_dec, total=t_dec)
        return balances

    async def create_order(self, request: OrderRequest) -> OrderResult:
        if self._read_only:
            raise ExchangeError("Exchange adapter is in read-only mode; live order submission is blocked.")
        if not self._api_key or not self._api_secret:
            raise ExchangeError("Exchange API credentials are not configured.")

        client = await self._ensure_connected()
        resolved = self._resolve_symbol(client, request.symbol)
        rules = await self.get_market_rules(request.symbol)
        if rules.contract_size > Decimal("0") and rules.contract_size != Decimal("1"):
            market_obj = client.market(resolved)
            contract_prec = _decimal_places((market_obj.get("precision") or {}).get("amount"), 0)
            qty = round_step_down(request.quantity / rules.contract_size, contract_prec)
            raw_min_contracts = Decimal(
                str(((market_obj.get("limits") or {}).get("amount") or {}).get("min") or 1)
            )
            if qty < raw_min_contracts:
                raise ExchangeError(
                    f"Rounded contracts {qty} is below exchange minimum {raw_min_contracts} for {request.symbol}."
                )
        else:
            qty = round_step_down(request.quantity, rules.quantity_precision)
            if qty < rules.min_quantity:
                raise ExchangeError(
                    f"Rounded quantity {qty} is below exchange minimum {rules.min_quantity} for {request.symbol}."
                )
        price_val = (
            float(round_step_down(request.price, rules.price_precision))
            if request.price is not None
            else None
        )

        order_type_up = request.order_type.upper()
        params: dict[str, Any] = {"newClientOrderId": request.client_order_id} if self.exchange_id == "binance" else {}
        if self.market_type == "FUTURES" and request.reduce_only:
            params["reduceOnly"] = True
        if request.stop_price is not None and request.stop_price > 0:
            stop_px = self._quantize(request.stop_price, precision=rules.price_precision)
            params["stopPrice"] = float(stop_px)
        if order_type_up in {"STOP_MARKET", "TAKE_PROFIT_MARKET"}:
            price_val = None
        try:
            res = await client.create_order(
                symbol=resolved,
                type=order_type_up.lower(),
                side=request.side.lower(),
                amount=float(qty),
                price=price_val,
                params=params,
            )
        except Exception as exc:
            raise ExchangeError(f"Order submission failed on {self.exchange_id}: {_safe_exc_summary(exc)}") from exc
        return self._map_order_result(res, request.client_order_id, request.symbol)

    async def cancel_order(self, exchange_order_id: str, symbol: str) -> OrderResult:
        if self._read_only:
            raise ExchangeError("Exchange adapter is in read-only mode.")
        client = await self._ensure_connected()
        resolved = self._resolve_symbol(client, symbol)
        try:
            res = await client.cancel_order(exchange_order_id, resolved)
        except Exception as exc:
            raise ExchangeError(f"Order cancel failed: {type(exc).__name__}") from exc
        return self._map_order_result(res, res.get("clientOrderId") or exchange_order_id, symbol)

    async def fetch_order(self, exchange_order_id: str, symbol: str) -> OrderResult:
        client = await self._ensure_connected()
        resolved = self._resolve_symbol(client, symbol)
        try:
            res = await client.fetch_order(exchange_order_id, resolved)
        except Exception as exc:
            raise ExchangeError(f"Fetch order failed: {type(exc).__name__}") from exc
        return self._map_order_result(res, res.get("clientOrderId") or exchange_order_id, symbol)

    async def fetch_my_trades(self, symbol: str, limit: int = 50) -> list[dict[str, Any]]:
        if not self._api_key or not self._api_secret:
            raise ExchangeError("Exchange API credentials are not configured.")
        client = await self._ensure_connected()
        resolved = self._resolve_symbol(client, symbol)
        try:
            return await client.fetch_my_trades(resolved, limit=limit)
        except Exception as exc:
            raise ExchangeError(f"Fetch trades failed: {type(exc).__name__}") from exc

    @staticmethod
    def _clean_symbol_key(raw_sym: str) -> str:
        return raw_sym.split(":")[0] if ":" in raw_sym else raw_sym

    async def list_tradable_markets(
        self, quote: str = "USDT", query: str = "", limit: int = 100
    ) -> list[dict[str, Any]]:
        client = await self._ensure_connected()
        q = query.strip().upper()
        norm_quote = quote.upper()
        seen: set[str] = set()
        results: list[dict[str, Any]] = []
        want_swap = self.market_type == "FUTURES"
        for m_sym, m in (client.markets or {}).items():
            if not m or not m.get("active", True):
                continue
            if str(m.get("quote") or "").upper() != norm_quote:
                continue
            if want_swap and not (m.get("swap") or m.get("future")):
                continue
            if m.get("expiry"):  # skip dated delivery contracts; keep perpetuals / spot
                continue
            clean = self._clean_symbol_key(m_sym)
            if clean in seen:
                continue
            base = str(m.get("base") or "").upper()
            if q and q not in clean.upper() and q not in base:
                continue
            seen.add(clean)
            notional_min = self._extract_min_notional(self.exchange_id, m)
            results.append(
                {
                    "symbol": clean,
                    "exchange_symbol": m_sym,
                    "base": base,
                    "quote": norm_quote,
                    "exchange": self.exchange_id,
                    "market_type": "FUTURES" if (m.get("swap") or m.get("future")) else "SPOT",
                    "min_notional": float(notional_min),
                }
            )
        results.sort(key=lambda r: (0 if q and r["base"] == q else 1, len(r["base"]), r["base"]))
        return results[: max(1, limit)]

    async def fetch_all_tickers(self, quote: str = "USDT") -> dict[str, Ticker]:
        client = await self._ensure_connected()
        norm_quote = quote.upper()
        try:
            raw_map = await client.fetch_tickers()
        except Exception as exc:
            raise ExchangeError(f"Batch ticker fetch failed on {self.exchange_id}: {type(exc).__name__}") from exc

        now_utc = datetime.now(timezone.utc)
        out: dict[str, Ticker] = {}
        markets = client.markets or {}
        want_swap = self.market_type == "FUTURES"
        for raw_sym, t in (raw_map or {}).items():
            m = markets.get(raw_sym)
            if m is not None:
                if not m.get("active", True):
                    continue
                if str(m.get("quote") or "").upper() != norm_quote:
                    continue
                if want_swap and not (m.get("swap") or m.get("future")):
                    continue
                if m.get("expiry"):
                    continue
            clean = self._clean_symbol_key(raw_sym)
            if not clean.endswith(f"/{norm_quote}"):
                continue
            last = Decimal(str(t.get("last") or t.get("close") or 0))
            if last <= 0:
                continue
            bid = Decimal(str(t.get("bid") or last))
            ask = Decimal(str(t.get("ask") or last))
            vol = Decimal(str(t.get("quoteVolume") or 0))
            pct = t.get("percentage")
            ts_ms = t.get("timestamp")
            ts = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc) if ts_ms else now_utc
            tk = Ticker(symbol=clean, last=last, bid=bid, ask=ask, volume_24h=vol, timestamp=ts)
            # Attach 24h change cleanly via raw percentage if needed later
            object.__setattr__(tk, "_pct_change", float(pct)) if pct is not None else None
            out[clean] = tk
        return out

    @staticmethod
    def _map_order_result(res: dict[str, Any], fallback_client_id: str, symbol: str) -> OrderResult:
        status_map = {
            "open": "OPEN",
            "closed": "FILLED",
            "canceled": "CANCELED",
            "cancelled": "CANCELED",
            "rejected": "REJECTED",
            "expired": "CANCELED",
        }
        raw_status = str(res.get("status") or "open").lower()
        filled = Decimal(str(res.get("filled") or 0))
        amount = Decimal(str(res.get("amount") or 0))
        mapped_status = status_map.get(raw_status, "OPEN")
        if mapped_status == "OPEN" and filled > 0 and filled < amount:
            mapped_status = "PARTIAL"
        fee_info = res.get("fee") or {}
        ts_ms = res.get("timestamp")
        ts = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc) if ts_ms else datetime.now(timezone.utc)
        avg = res.get("average") or res.get("price")
        return OrderResult(
            exchange_order_id=str(res.get("id") or ""),
            client_order_id=str(res.get("clientOrderId") or fallback_client_id),
            symbol=res.get("symbol") or symbol,
            side=str(res.get("side") or "BUY").upper(),
            order_type=str(res.get("type") or "MARKET").upper(),
            status=mapped_status,
            requested_quantity=amount,
            filled_quantity=filled,
            average_price=Decimal(str(avg)) if avg else None,
            fee=Decimal(str(fee_info.get("cost") or 0)),
            fee_asset=str(fee_info.get("currency") or "USDT"),
            timestamp=ts,
            raw={"id": res.get("id"), "status": raw_status},
        )
