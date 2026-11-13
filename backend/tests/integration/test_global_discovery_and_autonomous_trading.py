"""Tests for global coin discovery, anti-UNKNOWN regime classification, cold-start auto trading, and trailing profit lock."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import math

from httpx import ASGITransport, AsyncClient
import pandas as pd
import pytest

from app.config import Settings, TradingMode
from app.database.repositories.trading_repo import TradingRepository
from app.exchanges.base_exchange import ExchangeBalance, MarketRules, OrderBook, OrderBookLevel, Ticker
from app.exchanges.ccxt_adapter import CCXTExchangeAdapter
from app.intelligence.derivatives_intelligence import DerivativesIntelligenceService, DerivativesSentimentReport
from app.intelligence.regime_detector import MarketRegime, MarketRegimeDetector
from app.main import bootstrap_initial_state, create_app
from app.market.universe_discovery import candidate_symbol_aliases, normalize_pair
from app.workers.trading_worker import AutonomousTradingLoop


def _build_oscillating_uptrend_candles(bars: int = 260) -> list[dict]:
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    step = timedelta(minutes=15)
    candles: list[dict] = []
    px = 100.0
    for i in range(bars):
        phase = i % 26
        if i < bars - 14:
            if phase < 16:
                # 16-bar upward impulse (~ +4.5% move)
                wave = 0.0026 + (0.0004 if phase < 6 else 0.0)
            else:
                # 10-bar healthy consolidation cooling RSI back to ~50 without stopping out on 1.5*ATR
                wave = -0.0018 if phase < 23 else 0.0006
        elif i < bars - 5:
            wave = -0.0016 if i % 2 == 0 else 0.0003
        else:
            wave = 0.0023 + 0.0002 * (i - (bars - 5))
        prev = px
        px = max(10.0, px * (1.0 + wave))
        hi = max(prev, px) * 1.0018
        lo = min(prev, px) * 0.9985
        in_impulse = (i < bars - 14 and 0 <= phase <= 6) or i >= bars - 6
        vol = 390.0 if in_impulse else (210.0 + (i % 4) * 15.0)
        candles.append(
            {
                "timestamp": now - step * (bars - 1 - i),
                "open": Decimal(str(round(prev, 4))),
                "high": Decimal(str(round(hi, 4))),
                "low": Decimal(str(round(lo, 4))),
                "close": Decimal(str(round(px, 4))),
                "volume": Decimal(str(vol)),
            }
        )
    return candles


def test_normalize_and_alias_any_global_coin():
    assert normalize_pair("pepe") == "PEPE/USDT"
    assert normalize_pair("wifusdt") == "WIF/USDT"
    assert normalize_pair("tao-usdt") == "TAO/USDT"
    assert normalize_pair("1000SHIB/USDT:USDT") == "1000SHIB/USDT"

    aliases = candidate_symbol_aliases("PEPE/USDT")
    assert "PEPE/USDT" in aliases
    assert "1000PEPE/USDT" in aliases


def test_regime_detector_eliminates_false_unknown_on_valid_candles():
    candles = _build_oscillating_uptrend_candles(120)
    # Build transitional ADX / spread structure that previously fell into UNKNOWN
    df = pd.DataFrame(
        [
            {
                "timestamp": c["timestamp"],
                "open": float(c["open"]),
                "high": float(c["high"]),
                "low": float(c["low"]),
                "close": float(c["close"]),
                "volume": float(c["volume"]),
            }
            for c in candles[:90]
        ]
    )
    regime = MarketRegimeDetector().detect(df)
    assert regime.primary_regime != MarketRegime.UNKNOWN
    assert regime.primary_regime in {
        MarketRegime.BULLISH_TRENDING,
        MarketRegime.BEARISH_TRENDING,
        MarketRegime.SIDEWAYS,
        MarketRegime.VOLATILITY_EXPANSION,
        MarketRegime.VOLATILITY_CONTRACTION,
        MarketRegime.LOW_VOLATILITY,
    }


@pytest.mark.asyncio
async def test_global_search_watchlist_and_custom_coin_chart_endpoint(db_manager, monkeypatch):
    async def fake_all_tickers(self, quote="USDT"):
        return {
            "PEPE/USDT": Ticker(
                symbol="PEPE/USDT",
                last=Decimal("0.000012"),
                bid=Decimal("0.0000119"),
                ask=Decimal("0.0000121"),
                volume_24h=Decimal("420000000"),
                timestamp=datetime.now(timezone.utc),
            ),
            "WIF/USDT": Ticker(
                symbol="WIF/USDT",
                last=Decimal("2.15"),
                bid=Decimal("2.14"),
                ask=Decimal("2.16"),
                volume_24h=Decimal("180000000"),
                timestamp=datetime.now(timezone.utc),
            ),
        }

    async def fake_list_markets(self, quote="USDT", query="", limit=100):
        all_m = [
            {"symbol": "PEPE/USDT", "exchange_symbol": "1000PEPE/USDT:USDT", "base": "PEPE", "quote": "USDT", "exchange": "binance", "market_type": "FUTURES", "min_notional": 5.0},
            {"symbol": "WIF/USDT", "exchange_symbol": "WIF/USDT:USDT", "base": "WIF", "quote": "USDT", "exchange": "binance", "market_type": "FUTURES", "min_notional": 5.0},
            {"symbol": "KAS/USDT", "exchange_symbol": "KAS/USDT:USDT", "base": "KAS", "quote": "USDT", "exchange": "bybit", "market_type": "FUTURES", "min_notional": 5.0},
        ]
        q = query.strip().upper()
        return [m for m in all_m if not q or q in m["symbol"] or q in m["base"]][:limit]

    async def fake_ohlcv(self, symbol, timeframe="15m", since=None, limit=120):
        return _build_oscillating_uptrend_candles(limit)

    async def fake_ticker(self, symbol):
        return Ticker(
            symbol=symbol,
            last=Decimal("2.15"),
            bid=Decimal("2.149"),
            ask=Decimal("2.151"),
            volume_24h=Decimal("180000000"),
            timestamp=datetime.now(timezone.utc),
        )

    async def fake_market_rules(self, symbol):
        return MarketRules(
            symbol=symbol,
            base_asset=symbol.split("/")[0],
            quote_asset="USDT",
            min_quantity=Decimal("0.1"),
            min_notional=Decimal("5.0"),
            quantity_precision=2,
            price_precision=4,
            maker_fee=Decimal("0.0002"),
            taker_fee=Decimal("0.0005"),
            is_active=True,
        )

    async def fake_order_book(self, symbol, limit=20):
        return OrderBook(
            symbol=symbol,
            bids=[OrderBookLevel(price=Decimal("2.149"), quantity=Decimal("1000"))],
            asks=[OrderBookLevel(price=Decimal("2.151"), quantity=Decimal("1000"))],
            timestamp=datetime.now(timezone.utc),
        )

    async def fake_derivatives(self, symbol, df=None):
        return DerivativesSentimentReport(symbol=symbol, source="test_mock")

    async def fake_close(self):
        return None

    from app.market.universe_discovery import _MARKET_CATALOG_CACHE, _TICKER_UNIVERSE_CACHE

    _TICKER_UNIVERSE_CACHE.clear()
    _MARKET_CATALOG_CACHE.clear()
    monkeypatch.setattr(CCXTExchangeAdapter, "fetch_all_tickers", fake_all_tickers)
    monkeypatch.setattr(CCXTExchangeAdapter, "list_tradable_markets", fake_list_markets)
    monkeypatch.setattr(CCXTExchangeAdapter, "get_market_rules", fake_market_rules)
    monkeypatch.setattr(CCXTExchangeAdapter, "fetch_order_book", fake_order_book)
    monkeypatch.setattr(CCXTExchangeAdapter, "fetch_ohlcv", fake_ohlcv)
    monkeypatch.setattr(CCXTExchangeAdapter, "fetch_ticker", fake_ticker)
    monkeypatch.setattr(CCXTExchangeAdapter, "close", fake_close)
    monkeypatch.setattr(DerivativesIntelligenceService, "fetch_derivatives_sentiment", fake_derivatives)

    await bootstrap_initial_state()
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        tok_res = await client.post(
            "/api/auth/token",
            data={"username": "admin", "password": "admin-dev-password-change-me"},
        )
        headers = {"Authorization": f"Bearer {tok_res.json()['access_token']}"}

        # Search for coin outside the static 16 coins
        res_search = await client.get("/api/markets/search?q=WIF", headers=headers)
        assert res_search.status_code == 200
        syms = [m["symbol"] for m in res_search.json()["markets"]]
        assert "WIF/USDT" in syms

        # Pin custom coin WIF/USDT to global watchlist
        res_pin = await client.post(
            "/api/markets/watchlist",
            headers=headers,
            json={"symbol": "wif", "action": "add"},
        )
        assert res_pin.status_code == 200
        assert "WIF/USDT" in res_pin.json()["symbols"]

        # Load chart & analytics directly for arbitrary coin KAS/USDT
        res_mkt = await client.get("/api/market/KAS-USDT?timeframe=15m&limit=100", headers=headers)
        assert res_mkt.status_code == 200
        assert res_mkt.json()["symbol"] == "KAS/USDT"
        assert len(res_mkt.json()["candles"]) >= 50


@pytest.mark.asyncio
async def test_autonomous_loop_opens_trade_without_override_and_locks_trailing_profit(db_manager, mock_exchange):
    candles = _build_oscillating_uptrend_candles(260)
    for sym in ["BTC/USDT", "ETH/USDT", "SOL/USDT", "WIF/USDT"]:
        mock_exchange.candles_by_symbol[sym] = [dict(c) for c in candles]

    mock_exchange.balances["USDT"] = ExchangeBalance(
        asset="USDT", free=Decimal("500.0"), used=Decimal("0"), total=Decimal("500.0")
    )

    settings = Settings(
        trading_mode=TradingMode.PAPER,
        initial_capital_usd=Decimal("500.00"),
        trading_universe="BTC/USDT,ETH/USDT,SOL/USDT,WIF/USDT",
    )
    loop = AutonomousTradingLoop(exchange=mock_exchange, settings=settings)

    async with db_manager.session() as session:
        repo = TradingRepository(session)
        acc = await repo.get_or_create_default_account(initial_capital=Decimal("500.00"))
        await repo.upsert_balance(acc.id, "USDT", Decimal("500.00"))
        acc.is_trading_active = True
        acc.initial_capital = Decimal("500.00")
        acc.peak_equity = Decimal("500.00")
        await session.flush()

        # Run autonomous cycle WITHOUT strategy_stats_override (cold-start historical OOS validation must unlock trades!)
        res = await loop.run_cycle(session, allow_historical=True)
        assert res["survival_state"] in {"NORMAL", "CAUTION", "RECOVERY"}
        assert res["orders_submitted"] >= 1

        open_pos = await repo.get_open_positions(acc.id)
        assert len(open_pos) >= 1
        pos = open_pos[0]
        entry_px = pos.entry_price
        orig_qty = pos.quantity
        assert pos.stop_loss is not None and pos.take_profit is not None
        stop_dist_pct = abs(entry_px - pos.stop_loss) / entry_px
        tp_dist_pct = abs(pos.take_profit - entry_px) / entry_px
        assert stop_dist_pct >= Decimal("0.0075")
        assert tp_dist_pct >= stop_dist_pct * Decimal("1.95")

        # Simulate runner surging beyond +1R -> triggers 50% SCALE_OUT_1R & moves stop to breakeven+fees
        surge_mult = Decimal("1") + (stop_dist_pct * Decimal("1.15"))
        surge_px = (entry_px * surge_mult).quantize(Decimal("0.01"))
        for sym in mock_exchange.candles_by_symbol:
            last_bar = dict(mock_exchange.candles_by_symbol[sym][-1])
            last_bar["close"] = surge_px
            last_bar["high"] = surge_px * Decimal("1.001")
            last_bar["timestamp"] = datetime.now(timezone.utc)
            mock_exchange.candles_by_symbol[sym][-1] = last_bar

        await loop.run_cycle(session, allow_historical=True)
        open_after_surge = await repo.get_open_positions(acc.id)
        assert len(open_after_surge) >= 1
        pos_after_surge = open_after_surge[0]
        # Stop loss must have ratcheted up to breakeven+fee protection and 50% scaled out!
        assert pos_after_surge.stop_loss is not None
        assert pos_after_surge.stop_loss > entry_px
        assert pos_after_surge.scaled_out is True
        assert pos_after_surge.quantity < orig_qty

        # Now simulate pullback into the protected breakeven/ATR trail stop -> runner closes automatically!
        pullback_px = (pos_after_surge.stop_loss * Decimal("0.999")).quantize(Decimal("0.01"))
        for sym in mock_exchange.candles_by_symbol:
            last_bar = dict(mock_exchange.candles_by_symbol[sym][-1])
            last_bar["close"] = pullback_px
            last_bar["low"] = pullback_px * Decimal("0.999")
            last_bar["timestamp"] = datetime.now(timezone.utc)
            mock_exchange.candles_by_symbol[sym][-1] = last_bar

        await loop.run_cycle(session, allow_historical=True)
        events = await repo.get_recent_events(limit=20)
        assert any("SCALE_OUT_1R" in ev.message or "SCALE_OUT_1R" in str(ev.payload) for ev in events)
        assert any(
            reason in ev.message or reason in str(ev.payload)
            for ev in events
            for reason in ("BREAKEVEN_LOCK", "ATR_TRAILING_STOP")
        )


@pytest.mark.asyncio
async def test_one_dollar_paper_trade_and_partial_profit_booking(db_manager, mock_exchange, monkeypatch):
    """Realistic PAPER mode enforces real $5 Binance minimums (rejecting $1 micro) and executes & partial-closes cleanly at $500."""
    from datetime import timedelta
    from app.database.repositories.trading_repo import TradingRepository
    from app.exchanges.base_exchange import MarketRules, OrderBook, OrderBookLevel, Ticker
    from app.intelligence.derivatives_intelligence import (
        DerivativesIntelligenceService,
        DerivativesSentimentReport,
    )

    # Prepare oscillating uptrend candles across BTC/ETH/SOL/BNB so separated OOS stats have sufficient trades offline
    bars = _build_oscillating_uptrend_candles(320)
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    for i, c in enumerate(bars):
        c["timestamp"] = now - timedelta(minutes=15 * (len(bars) - 1 - i))
    for sym in ("BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT"):
        mock_exchange.candles_by_symbol[sym] = [dict(c) for c in bars]

    async def fake_ohlcv(self, symbol: str, timeframe: str, since=None, limit: int = 200):
        norm = "BTC/USDT" if symbol.startswith("BTC") else symbol
        if norm in mock_exchange.candles_by_symbol:
            return mock_exchange.candles_by_symbol[norm][-limit:]
        return bars[-limit:]

    async def fake_ticker(self, symbol: str):
        last = bars[-1]["close"]
        return Ticker(
            symbol=symbol,
            last=last,
            bid=(last * Decimal("0.9999")).quantize(Decimal("0.01")),
            ask=(last * Decimal("1.0001")).quantize(Decimal("0.01")),
            volume_24h=Decimal("50000000"),
            timestamp=datetime.now(timezone.utc),
        )

    async def fake_order_book(self, symbol: str, limit: int = 20):
        last = bars[-1]["close"]
        return OrderBook(
            symbol=symbol,
            bids=[OrderBookLevel(price=last - Decimal(i + 1), quantity=Decimal("2.0")) for i in range(5)],
            asks=[OrderBookLevel(price=last + Decimal(i + 1), quantity=Decimal("2.0")) for i in range(5)],
            timestamp=datetime.now(timezone.utc),
        )

    async def fake_market_rules(self, symbol: str):
        return MarketRules(
            symbol=symbol,
            base_asset="BTC",
            quote_asset="USDT",
            min_quantity=Decimal("0.001"),
            min_notional=Decimal("5.0"),
            quantity_precision=3,
            price_precision=2,
            maker_fee=Decimal("0.0002"),
            taker_fee=Decimal("0.0005"),
            is_active=True,
        )

    async def fake_close(self):
        return None

    async def fake_deriv(self, symbol: str, df=None):
        return DerivativesSentimentReport(
            symbol=symbol,
            source="mock-offline",
            funding_rate_pct=0.008,
            derivatives_bias="NEUTRAL",
            open_interest_usd=250_000_000.0,
            long_short_ratio=1.05,
            crowding_state="BALANCED",
            allow_long=True,
            allow_short=True,
            rationale="Offline mock balanced derivatives state.",
        )

    monkeypatch.setattr(CCXTExchangeAdapter, "fetch_ohlcv", fake_ohlcv)
    monkeypatch.setattr(CCXTExchangeAdapter, "fetch_ticker", fake_ticker)
    monkeypatch.setattr(CCXTExchangeAdapter, "fetch_order_book", fake_order_book)
    monkeypatch.setattr(CCXTExchangeAdapter, "get_market_rules", fake_market_rules)
    monkeypatch.setattr(CCXTExchangeAdapter, "close", fake_close)
    monkeypatch.setattr(DerivativesIntelligenceService, "fetch_derivatives_sentiment", fake_deriv)

    await bootstrap_initial_state()
    app = create_app()
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        tok_res = await client.post(
            "/api/auth/token",
            data={"username": "admin", "password": "admin-dev-password-change-me"},
        )
        assert tok_res.status_code == 200
        headers = {"Authorization": f"Bearer {tok_res.json()['access_token']}"}

        # 1. With $1.00 capital on Binance PAPER, realistic exchange rules must reject rather than faking $0.25 fills
        micro_reset = await client.post(
            "/api/portfolio/reset-paper",
            headers=headers,
            json={"initial_capital": 1.0, "switch_to_paper": True},
        )
        assert micro_reset.status_code == 200
        start_res = await client.post("/api/trading/start", headers=headers)
        assert start_res.status_code == 200

        micro_trade = await client.post(
            "/api/trading/test-paper-trade?symbol=BTC/USDT&timeframe=15m&side=LONG",
            headers=headers,
        )
        assert micro_trade.status_code == 200
        assert micro_trade.json()["status"] == "rejected"

        # 2. Reset to realistic $500.00 PAPER capital -> real $5+ Binance minimum executes cleanly & supports partial close
        reset_res = await client.post(
            "/api/portfolio/reset-paper",
            headers=headers,
            json={"initial_capital": 500.0, "switch_to_paper": True},
        )
        assert reset_res.status_code == 200
        assert reset_res.json()["total_equity"] == pytest.approx(500.0)

        trade_res = await client.post(
            "/api/trading/test-paper-trade?symbol=BTC/USDT&timeframe=15m&side=LONG",
            headers=headers,
        )
        assert trade_res.status_code == 200
        trade_body = trade_res.json()
        assert trade_body["status"] == "executed", trade_body
        exec_order = trade_body["trade"]
        assert exec_order["notional_usd"] >= 5.0

        async with db_manager.session() as session:
            repo = TradingRepository(session)
            acc = await repo.get_or_create_default_account()
            open_positions = await repo.get_open_positions(acc.id)
            assert len(open_positions) == 1
            pos_id = str(open_positions[0].id)

        close_res = await client.post(
            f"/api/trading/positions/{pos_id}/close?fraction=0.5",
            headers=headers,
        )
        assert close_res.status_code == 200
        assert close_res.json()["status"] == "partially_booked"


@pytest.mark.asyncio
async def test_mexc_micro_contract_rules_and_one_dollar_sizing():
    """Verify contractSize/lotSizeFilter parsing on MEXC & Bybit plus $1.00 live SUI sizing."""
    from app.exchanges.credentials_service import MICRO_LIVE_FUTURES_EXCHANGES, min_live_order_usd
    from app.risk.position_sizer import PositionSizer

    class _FakeClient:
        def __init__(self, markets: dict[str, dict]):
            self.markets = markets

        def market(self, symbol: str) -> dict:
            return self.markets[symbol]

        async def load_markets(self):
            return self.markets

        async def close(self):
            return None

    mexc_adapter = CCXTExchangeAdapter(exchange_id="mexc", market_type="FUTURES", sandbox=False, read_only=True)
    mexc_adapter._client = _FakeClient(
        {
            "SUI/USDT:USDT": {
                "symbol": "SUI/USDT:USDT",
                "base": "SUI",
                "quote": "USDT",
                "active": True,
                "swap": True,
                "contractSize": 1.0,
                "limits": {"amount": {"min": 1.0}, "cost": {"min": None}},
                "precision": {"amount": 1.0, "price": 0.0001},
                "taker": 0.0002,
                "maker": 0.0,
                "info": {},
            },
            "DOGE/USDT:USDT": {
                "symbol": "DOGE/USDT:USDT",
                "base": "DOGE",
                "quote": "USDT",
                "active": True,
                "swap": True,
                "contractSize": 100.0,
                "limits": {"amount": {"min": 1.0}, "cost": {"min": None}},
                "precision": {"amount": 1.0, "price": 0.00001},
                "taker": 0.0002,
                "maker": 0.0,
                "info": {},
            },
        }
    )

    sui_rules = await mexc_adapter.get_market_rules("SUI/USDT")
    assert sui_rules.min_notional == Decimal("0")
    assert sui_rules.contract_size == Decimal("1")
    assert sui_rules.min_quantity == Decimal("1")
    assert sui_rules.quantity_precision == 0
    assert sui_rules.lot_step is None

    doge_rules = await mexc_adapter.get_market_rules("DOGE/USDT")
    assert doge_rules.contract_size == Decimal("100")
    assert doge_rules.min_quantity == Decimal("100")
    assert doge_rules.lot_step == Decimal("100")

    bybit_adapter = CCXTExchangeAdapter(exchange_id="bybit", market_type="FUTURES", sandbox=False, read_only=True)
    bybit_adapter._client = _FakeClient(
        {
            "SUI/USDT:USDT": {
                "symbol": "SUI/USDT:USDT",
                "base": "SUI",
                "quote": "USDT",
                "active": True,
                "swap": True,
                "contractSize": 1.0,
                "limits": {"amount": {"min": 1.0}, "cost": {"min": None}},
                "precision": {"amount": 1.0, "price": 0.0001},
                "taker": 0.00055,
                "maker": 0.0002,
                "info": {"lotSizeFilter": {"minNotionalValue": "5"}},
            }
        }
    )
    bybit_sui_rules = await bybit_adapter.get_market_rules("SUI/USDT")
    assert bybit_sui_rules.min_notional == Decimal("5")

    assert "mexc" in MICRO_LIVE_FUTURES_EXCHANGES
    assert "kucoinfutures" in MICRO_LIVE_FUTURES_EXCHANGES
    assert "gate" in MICRO_LIVE_FUTURES_EXCHANGES
    assert min_live_order_usd("mexc") == Decimal("1.00")
    assert min_live_order_usd("gate") == Decimal("1.00")
    assert min_live_order_usd("binance") == Decimal("5.00")

    gate_adapter = CCXTExchangeAdapter(exchange_id="gate", market_type="FUTURES", sandbox=False, read_only=True)
    gate_adapter._client = _FakeClient(
        {
            "ICP/USDT:USDT": {
                "symbol": "ICP/USDT:USDT",
                "base": "ICP",
                "quote": "USDT",
                "active": True,
                "swap": True,
                "contractSize": 0.001,
                "limits": {"amount": {"min": 1.0}, "cost": {"min": None}},
                "precision": {"amount": 1.0, "price": 0.001},
                "taker": 0.0005,
                "maker": 0.0002,
                "info": {},
            }
        }
    )
    icp_rules = await gate_adapter.get_market_rules("ICP/USDT")
    assert icp_rules.contract_size == Decimal("0.001")
    assert icp_rules.min_quantity == Decimal("0.001")
    assert icp_rules.lot_step == Decimal("0.001")

    sizer = PositionSizer()
    sizing = sizer.calculate(
        account_equity=Decimal("1.00"),
        available_balance=Decimal("1.00"),
        kelly_risk_fraction=Decimal("0.015"),
        entry_price=Decimal("0.80"),
        stop_loss_price=Decimal("0.7936"),
        rules=sui_rules,
    )
    assert sizing.is_viable is True, sizing.reason
    assert sizing.quantity == Decimal("1")
    assert sizing.capped_notional_usd == Decimal("0.800000")

    gate_sizing = sizer.calculate(
        account_equity=Decimal("1.00"),
        available_balance=Decimal("1.00"),
        kelly_risk_fraction=Decimal("0.015"),
        entry_price=Decimal("5.00"),
        stop_loss_price=Decimal("4.96"),
        rules=icp_rules,
    )
    assert gate_sizing.is_viable is True, gate_sizing.reason
    assert gate_sizing.capped_notional_usd >= Decimal("0.50")

    # Real $5.30 USDT Futures balance should allow a $5.00+ minimum order cleanly (with 5% isolated buffer)
    binance_sizing = sizer.calculate(
        account_equity=Decimal("5.30"),
        available_balance=Decimal("5.30"),
        kelly_risk_fraction=Decimal("0.015"),
        entry_price=Decimal("1.00"),
        stop_loss_price=Decimal("0.992"),
        rules=bybit_sui_rules,
    )
    assert binance_sizing.is_viable is True, binance_sizing.reason
    assert binance_sizing.capped_notional_usd >= Decimal("5.00")


@pytest.mark.asyncio
async def test_effective_isolated_leverage_and_manual_scan_below_five_dollars(db_manager, mock_exchange):
    """Any positive USDT balance (<$5) auto-scales isolated leverage conservatively, and manual scan runs even when paused."""
    from app.risk.position_sizer import effective_isolated_leverage

    rules = MarketRules(
        symbol="SOL/USDT",
        base_asset="SOL",
        quote_asset="USDT",
        min_quantity=Decimal("0.01"),
        min_notional=Decimal("5.0"),
        quantity_precision=2,
        price_precision=2,
        maker_fee=Decimal("0.0002"),
        taker_fee=Decimal("0.0005"),
        is_active=True,
    )
    assert effective_isolated_leverage(1, Decimal("4.95"), rules, reference_price=Decimal("100")) == 2
    assert effective_isolated_leverage(1, Decimal("5.50"), rules, reference_price=Decimal("100")) == 1

    candles = _build_oscillating_uptrend_candles(260)
    universe = ["SOL/USDT", "WIF/USDT", "PEPE/USDT", "DOGE/USDT", "SUI/USDT"]
    for sym in universe:
        mock_exchange.candles_by_symbol[sym] = [dict(c) for c in candles]
    mock_exchange.balances["USDT"] = ExchangeBalance(
        asset="USDT", free=Decimal("4.95"), used=Decimal("0"), total=Decimal("4.95")
    )

    settings = Settings(
        trading_mode=TradingMode.PAPER,
        initial_capital_usd=Decimal("4.95"),
        trading_universe=",".join(universe),
    )
    loop = AutonomousTradingLoop(exchange=mock_exchange, settings=settings)

    async with db_manager.session() as session:
        repo = TradingRepository(session)
        acc = await repo.get_or_create_default_account(initial_capital=Decimal("4.95"))
        await repo.upsert_balance(acc.id, "USDT", Decimal("4.95"))
        acc.is_trading_active = False
        acc.initial_capital = Decimal("4.95")
        acc.peak_equity = Decimal("4.95")
        acc.market_type = "FUTURES"
        acc.leverage = 1
        await session.flush()

        res = await loop.run_cycle(session, allow_historical=True, manual_trigger=True)
        assert res["orders_submitted"] >= 1, res
        assert len(res["executed_trades"]) >= 1
        open_pos = await repo.get_open_positions(acc.id)
        assert len(open_pos) >= 1
        assert open_pos[0].leverage == 2


@pytest.mark.asyncio
async def test_live_reconciliation_ignores_stale_paper_orders(db_manager, mock_exchange):
    """Leftover PAPER partial orders must never cause LIVE_READ_ONLY/LIVE_TRADING reconciliation to HALT."""
    from app.database.models import Order
    from app.execution.reconciliation import ReconciliationService

    async def fail_paper_fetch(order_id: str, symbol: str):
        raise AssertionError(f"Simulated order {order_id} should never query live exchange!")

    mock_exchange.fetch_order = fail_paper_fetch  # type: ignore[method-assign]
    mock_exchange.balances["USDT"] = ExchangeBalance(
        asset="USDT", free=Decimal("4.95"), used=Decimal("0"), total=Decimal("4.95")
    )

    async with db_manager.session() as session:
        repo = TradingRepository(session)
        acc = await repo.get_or_create_default_account(initial_capital=Decimal("4.95"))
        await repo.upsert_balance(acc.id, "USDT", Decimal("4.95"))
        stale_paper = Order(
            account_id=acc.id,
            client_order_id="paper-client-stale-1",
            exchange_order_id="paper-stale-1",
            exchange="binance",
            symbol="BTC/USDT",
            side="BUY",
            order_type="MARKET",
            status="PARTIAL",
            requested_quantity=Decimal("0.01"),
            filled_quantity=Decimal("0.005"),
            fee_paid=Decimal("0"),
            trading_mode=TradingMode.PAPER.value,
        )
        session.add(stale_paper)
        await session.flush()

        recon = await ReconciliationService().reconcile(
            session=session,
            account_id=acc.id,
            exchange=mock_exchange,
            trading_mode=TradingMode.LIVE_READ_ONLY,
        )
        assert recon.verified is True, recon.discrepancies
        assert stale_paper.status == "CANCELED"


