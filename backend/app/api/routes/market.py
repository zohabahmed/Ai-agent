"""Market data inspection API endpoints with OpenBB, TradingView Technicals & Real-Time Derivatives Sentiment."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user, get_db_session, get_market_cache, rate_limit_guard
from app.config import get_settings
from app.database.models import User
from app.database.repositories.trading_repo import TradingRepository
from app.exchanges.credentials_service import ExchangeConnectionService
from app.intelligence.derivatives_intelligence import DerivativesIntelligenceService
from app.intelligence.openbb_research import OpenBBQuantResearch
from app.intelligence.regime_detector import MarketRegimeDetector
from app.market.candle_manager import CandleManager
from app.market.data_collector import MarketDataCollector
from app.market.market_cache import MarketCache
from app.market.universe_discovery import (
    GlobalMarketDiscoveryService,
    get_pinned_watchlist,
    normalize_pair,
    update_pinned_watchlist,
)

router = APIRouter(tags=["Market"])


class WatchlistUpdateBody(BaseModel):
    symbol: str
    action: str = "add"  # add or remove


@router.get("/markets/search", dependencies=[Depends(rate_limit_guard)])
async def search_global_markets(
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    q: str = "",
    limit: int = 60,
) -> dict[str, Any]:
    """Search ANY coin across active USD-M Futures plus global Spot/Swap venues."""
    settings = get_settings()
    account = await TradingRepository(session).get_or_create_default_account()
    ex = await ExchangeConnectionService.build_exchange_for_account(
        session, account, settings=settings, force_read_only=True
    )
    try:
        results = await GlobalMarketDiscoveryService.search_global_coins(
            ex, query=q, limit=min(limit, 150), include_external=True
        )
    finally:
        await ex.close()
    return {"query": q, "count": len(results), "markets": results}


@router.get("/markets/watchlist", dependencies=[Depends(rate_limit_guard)])
async def list_watchlist(
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    cache: Annotated[MarketCache, Depends(get_market_cache)],
) -> dict[str, Any]:
    symbols = await get_pinned_watchlist(session, cache)
    return {"symbols": symbols}


@router.post("/markets/watchlist", dependencies=[Depends(rate_limit_guard)])
async def mutate_watchlist(
    body: WatchlistUpdateBody,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    cache: Annotated[MarketCache, Depends(get_market_cache)],
) -> dict[str, Any]:
    symbols = await update_pinned_watchlist(session, cache, body.symbol, action=body.action)
    return {"symbols": symbols, "updated": normalize_pair(body.symbol), "action": body.action}


@router.get("/market/{symbol:path}", dependencies=[Depends(rate_limit_guard)])
async def get_market_symbol(
    symbol: str,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    cache: Annotated[MarketCache, Depends(get_market_cache)],
    timeframe: str = "15m",
    limit: int = 120,
) -> dict[str, Any]:
    """Return recent candles (enriched with EMA/BB/RSI), regime, OpenBB quant, TradingView rating, and CoinGlass/Binance Futures derivatives sentiment."""
    norm_symbol = normalize_pair(symbol)
    settings = get_settings()
    repo = TradingRepository(session)
    account = await repo.get_or_create_default_account()
    ex_id = account.exchange or settings.exchange_id
    candles = await repo.get_candles(ex_id, norm_symbol, timeframe, limit=min(limit, 500))
    if not candles and timeframe != "15m":
        candles = await repo.get_candles(ex_id, norm_symbol, "15m", limit=min(limit, 500))

    resolved_symbol = norm_symbol
    if not candles:
        # Auto-seed live candles from exchange (or global public venue fallback) for ANY coin in the world
        ex = await ExchangeConnectionService.build_exchange_for_account(
            session, account, settings=settings, force_read_only=True
        )
        try:
            collector = MarketDataCollector(ex, cache=cache, settings=settings)
            snap, resolved_symbol = await GlobalMarketDiscoveryService.collect_symbol_snapshot_any(
                collector,
                session,
                norm_symbol,
                timeframe=timeframe,
                limit=min(limit, 200),
                allow_historical=True,
                allow_external_feed=True,
            )
            candles = await repo.get_candles(ex_id, resolved_symbol, timeframe, limit=min(limit, 500))
            if not candles and snap.validation.valid_candles:
                # External venue saved under its own exchange_id; persist copy under primary chart key too
                await repo.save_candles(
                    exchange=ex_id,
                    symbol=norm_symbol,
                    timeframe=timeframe,
                    candles=snap.validation.valid_candles,
                )
                candles = await repo.get_candles(ex_id, norm_symbol, timeframe, limit=min(limit, 500))
                resolved_symbol = norm_symbol
        except Exception:
            candles = []
        finally:
            await ex.close()

    df = CandleManager.to_dataframe(candles)

    detector = MarketRegimeDetector()
    regime = detector.detect(df)
    openbb_quant = OpenBBQuantResearch().analyze(df).to_dict()

    deriv_service = DerivativesIntelligenceService()
    tv_rating = deriv_service.compute_tradingview_rating(df).to_dict()
    deriv_sentiment = await deriv_service.fetch_derivatives_sentiment(resolved_symbol, df)
    enriched_candles = deriv_service.enrich_candles_for_chart(candles)

    cached_ticker = await cache.get_json(f"market:ticker:{resolved_symbol}") or await cache.get_json(
        f"market:ticker:{norm_symbol}"
    )
    if cached_ticker is None and enriched_candles:
        last_px = enriched_candles[-1]["close"]
        cached_ticker = {
            "symbol": resolved_symbol,
            "last": last_px,
            "bid": last_px,
            "ask": last_px,
        }

    return {
        "symbol": resolved_symbol,
        "timeframe": timeframe,
        "ticker": cached_ticker,
        "openbb_quant": openbb_quant,
        "tradingview_rating": tv_rating,
        "derivatives_sentiment": deriv_sentiment.to_dict(),
        "regime": {
            "regime": regime.primary_regime.value,
            "confidence": float(regime.confidence),
            "adx": float(regime.trend_strength_adx),
            "atr_pct": float(regime.volatility_pct),
            "volatility_ratio": float(regime.volatility_pct),
            "trend_slope": float(regime.trend_slope),
        },
        "candles": enriched_candles,
    }
