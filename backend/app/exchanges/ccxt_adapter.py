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
    ExchangePosition,
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

        futures_wallet_map: dict[str, tuple[Decimal, Decimal]] = {}
        if self.market_type == "FUTURES":
            info = raw.get("info") if isinstance(raw.get("info"), dict) else {}
            assets_info = info.get("assets") if isinstance(info.get("assets"), list) else []
            for entry in assets_info:
                if not isinstance(entry, dict):
                    continue
                asset_code = str(entry.get("asset") or "").upper()
                if not asset_code:
                    continue
                try:
                    wb = Decimal(str(entry.get("walletBalance") or 0))
                    ab = Decimal(str(entry.get("availableBalance") or 0))
                except Exception:
                    continue
                if wb > 0 or ab > 0:
                    futures_wallet_map[asset_code] = (wb, ab)

        all_assets = (
            set(total_map.keys())
            | set(free_map.keys())
            | set(used_map.keys())
            | set(futures_wallet_map.keys())
        )
        for asset in all_assets:
            t_dec = Decimal(str(total_map.get(asset, 0) or 0))
            f_dec = Decimal(str(free_map.get(asset, 0) or 0))
            u_dec = Decimal(str(used_map.get(asset, 0) or 0))
            if asset in futures_wallet_map:
                wb_dec, ab_dec = futures_wallet_map[asset]
                f_dec = ab_dec if ab_dec >= 0 else f_dec
                u_dec = max(Decimal("0"), wb_dec - f_dec)
                t_dec = max(wb_dec, f_dec + u_dec)
            elif self.market_type == "FUTURES" and t_dec > f_dec + u_dec:
                u_dec = max(u_dec, t_dec - f_dec)
                t_dec = f_dec + u_dec
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
        if self.exchange_id == "binance" and order_type_up == "MARKET":
            params["newOrderRespType"] = "RESULT" if self.market_type == "FUTURES" else "FULL"
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

        result = self._map_order_result(res, request.client_order_id, request.symbol)
        # Binance / CCXT MARKET responses can return avgPrice=0 (ACK) before matching finishes.
        if order_type_up == "MARKET" and result.exchange_order_id and (
            result.average_price is None or result.average_price <= 0 or result.filled_quantity <= 0
        ):
            for _ in range(2):
                try:
                    await asyncio.sleep(0.2)
                    fetched = await self.fetch_order(result.exchange_order_id, request.symbol)
                    if fetched.average_price and fetched.average_price > 0:
                        result = fetched
                        break
                    if fetched.filled_quantity > result.filled_quantity:
                        result = fetched
                except Exception:
                    break
            if (result.average_price is None or result.average_price <= 0) and result.filled_quantity > 0:
                try:
                    tk = await self.fetch_ticker(request.symbol)
                    if tk.last > 0:
                        result = OrderResult(
                            exchange_order_id=result.exchange_order_id,
                            client_order_id=result.client_order_id,
                            symbol=result.symbol,
                            side=result.side,
                            order_type=result.order_type,
                            status=result.status,
                            requested_quantity=result.requested_quantity,
                            filled_quantity=result.filled_quantity,
                            average_price=tk.last,
                            fee=result.fee,
                            fee_asset=result.fee_asset,
                            timestamp=result.timestamp,
                            raw=result.raw,
                        )
                except Exception:
                    pass
        return result

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

    async def fetch_positions(self) -> list[ExchangePosition]:
        if self.market_type != "FUTURES":
            return []
        if not self._api_key or not self._api_secret:
            raise ExchangeError("Exchange API credentials are not configured.")
        client = await self._ensure_connected()
        try:
            raw_positions = await client.fetch_positions()
        except Exception as exc:
            raise ExchangeError(f"Position fetch failed on {self.exchange_id}: {type(exc).__name__}") from exc

        out: list[ExchangePosition] = []
        markets = client.markets or {}
        for p in raw_positions or []:
            if not isinstance(p, dict):
                continue
            info = p.get("info") if isinstance(p.get("info"), dict) else {}
            contracts_raw = p.get("contracts")
            if contracts_raw is None:
                contracts = Decimal(str(info.get("positionAmt") or 0))
            else:
                contracts = Decimal(str(contracts_raw or 0))
            if contracts == 0:
                continue

            raw_sym = str(p.get("symbol") or "")
            clean_sym = self._clean_symbol_key(raw_sym)
            if not clean_sym:
                continue

            market_obj = markets.get(raw_sym) or markets.get(clean_sym) or {}
            contract_size = Decimal(str(p.get("contractSize") or market_obj.get("contractSize") or 1))
            if contract_size <= 0:
                contract_size = Decimal("1")

            qty = abs(contracts) * contract_size
            side_raw = str(p.get("side") or "").upper()
            side = "SHORT" if (side_raw in {"SHORT", "SELL"} or contracts < 0) else "LONG"

            entry_px = Decimal(str(p.get("entryPrice") or info.get("entryPrice") or 0))
            mark_px = Decimal(str(p.get("markPrice") or info.get("markPrice") or entry_px or 0))
            notional = abs(Decimal(str(p.get("notional") or info.get("notional") or (qty * mark_px) or 0)))

            isolated_wallet = Decimal(str(info.get("isolatedWallet") or 0))
            initial_margin = Decimal(
                str(p.get("initialMargin") or info.get("positionInitialMargin") or info.get("initialMargin") or 0)
            )
            collateral = Decimal(str(p.get("collateral") or 0))
            margin_mode = str(
                p.get("marginMode")
                or ("isolated" if str(info.get("isolated") or "").lower() == "true" or isolated_wallet > 0 else "cross")
            ).lower()
            if isolated_wallet > 0:
                margin_used = isolated_wallet
            elif initial_margin > 0:
                margin_used = initial_margin
            else:
                margin_used = collateral

            raw_lev = p.get("leverage") or info.get("leverage")
            if raw_lev is not None and Decimal(str(raw_lev)) > 0:
                leverage = Decimal(str(raw_lev))
            elif initial_margin > 0 and notional > 0:
                leverage = max(Decimal("1"), (notional / initial_margin).quantize(Decimal("1")))
            elif margin_used > 0 and notional > 0:
                leverage = max(Decimal("1"), (notional / margin_used).quantize(Decimal("1")))
            else:
                leverage = Decimal(str(self.leverage or 1))

            u_pnl_raw = p.get("unrealizedPnl")
            if u_pnl_raw is None:
                u_pnl_raw = info.get("unRealizedProfit")
            u_pnl = Decimal(str(u_pnl_raw or 0))

            liq_raw = p.get("liquidationPrice") or info.get("liquidationPrice")
            liq_px: Decimal | None = None
            if liq_raw not in (None, "", 0, "0"):
                liq_dec = Decimal(str(liq_raw))
                if liq_dec > 0:
                    liq_px = liq_dec

            sl_raw = p.get("stopLossPrice")
            tp_raw = p.get("takeProfitPrice")
            out.append(
                ExchangePosition(
                    symbol=clean_sym,
                    side=side,
                    quantity=qty,
                    entry_price=entry_px,
                    mark_price=mark_px,
                    margin_used=margin_used,
                    leverage=leverage,
                    unrealized_pnl=u_pnl,
                    notional=notional,
                    liquidation_price=liq_px,
                    margin_mode=margin_mode,
                    stop_loss_price=Decimal(str(sl_raw)) if sl_raw else None,
                    take_profit_price=Decimal(str(tp_raw)) if tp_raw else None,
                )
            )
        return out

    async def fetch_open_orders(self, symbol: str | None = None) -> list[OrderResult]:
        if not self._api_key or not self._api_secret:
            raise ExchangeError("Exchange API credentials are not configured.")
        client = await self._ensure_connected()
        resolved = self._resolve_symbol(client, symbol) if symbol else None
        try:
            raw_orders = await client.fetch_open_orders(resolved) if resolved else await client.fetch_open_orders()
        except Exception as exc:
            raise ExchangeError(f"Open orders fetch failed on {self.exchange_id}: {type(exc).__name__}") from exc
        out: list[OrderResult] = []
        for raw in raw_orders or []:
            if not isinstance(raw, dict):
                continue
            raw_sym = self._clean_symbol_key(str(raw.get("symbol") or symbol or ""))
            out.append(self._map_order_result(raw, str(raw.get("clientOrderId") or ""), raw_sym))
        return out

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
        info = res.get("info") if isinstance(res.get("info"), dict) else {}
        raw_avg = res.get("average") or res.get("price") or info.get("avgPrice") or info.get("price")
        avg_dec: Decimal | None = None
        if raw_avg not in (None, "", 0, "0"):
            try:
                candidate = Decimal(str(raw_avg))
                if candidate > 0:
                    avg_dec = candidate
            except Exception:
                avg_dec = None
        if avg_dec is None and filled > 0:
            cost_val = res.get("cost") or info.get("cumQuote") or info.get("cummulativeQuoteQty")
            if cost_val not in (None, "", 0, "0"):
                try:
                    cost_dec = Decimal(str(cost_val))
                    if cost_dec > 0:
                        avg_dec = (cost_dec / filled).quantize(Decimal("0.00000001"))
                except Exception:
                    pass
        return OrderResult(
            exchange_order_id=str(res.get("id") or ""),
            client_order_id=str(res.get("clientOrderId") or fallback_client_id),
            symbol=CCXTExchangeAdapter._clean_symbol_key(str(res.get("symbol") or symbol)),
            side=str(res.get("side") or "BUY").upper(),
            order_type=str(res.get("type") or "MARKET").upper(),
            status=mapped_status,
            requested_quantity=amount,
            filled_quantity=filled,
            average_price=avg_dec,
            fee=Decimal(str(fee_info.get("cost") or 0)),
            fee_asset=str(fee_info.get("currency") or "USDT"),
            timestamp=ts,
            raw={
                "id": res.get("id"),
                "status": raw_status,
                "stopPrice": res.get("stopPrice") or res.get("triggerPrice") or info.get("stopPrice"),
            },
        )
