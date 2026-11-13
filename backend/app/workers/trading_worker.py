"""Trading Worker implementing the 22-step Autonomous Survival-First Operating Loop."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.adaptive.drift_detector import DriftDetector
from app.adaptive.strategy_selector import StrategySelector
from app.backtesting.performance_metrics import TradeRecord
from app.config import Settings, TradingMode, get_settings
from app.database.models import Account, Position, RiskDecision, TradeSignal
from app.database.repositories.trading_repo import TradingRepository
from app.database.session import get_db_manager
from app.exchanges.base_exchange import BaseExchange, OrderRequest
from app.exchanges.credentials_service import ExchangeConnectionService
from app.exchanges.exchange_factory import ExchangeFactory
from app.exchanges.exchange_health import ExchangeHealthMonitor
from app.execution.execution_engine import ExecutionEngine
from app.execution.exit_policy import (
    clamp_quick_targets,
    evaluate_position_exit_step,
    extract_atr_value,
    normalize_atr_targets,
)
from app.execution.order_manager import OrderManager
from app.execution.protective_orders import (
    cancel_protective_orders,
    detect_triggered_protective_fill,
    place_protective_orders,
    sync_stop_loss_order,
)
from app.execution.reconciliation import ReconciliationService
from app.intelligence.ai_advisor import AIAdvisor
from app.intelligence.confidence_estimator import ConfidenceEstimator
from app.intelligence.derivatives_intelligence import DerivativesIntelligenceService
from app.intelligence.openbb_research import OpenBBQuantResearch
from app.intelligence.opportunity_engine import EvaluatedOpportunity, OpportunityEngine
from app.intelligence.regime_detector import MarketRegime, MarketRegimeDetector
from app.intelligence.strategy_validation_service import (
    StrategyValidationStats,
    check_phase2_entry_filters,
    evaluate_separated_oos_stats,
)
from app.market.data_collector import MarketDataCollector
from app.market.market_cache import MarketCache
from app.market.universe_discovery import (
    GlobalMarketDiscoveryService,
    get_pinned_watchlist,
)
from app.portfolio.balance_manager import BalanceManager
from app.portfolio.equity_calculator import EquityCalculator
from app.portfolio.portfolio_manager import PortfolioManager
from app.risk.daily_baseline import sync_utc_day_baseline
from app.risk.position_sizer import effective_isolated_leverage
from app.risk.risk_manager import RiskManager
from app.risk.survival_controller import SurvivalController
from app.strategies.strategy_registry import StrategyRegistry


_CLAMP_COMPAT = clamp_quick_targets


class AutonomousTradingLoop:
    """
    Executes the 22-step autonomous cycle:
    1. Check system health -> 2. Retrieve balances -> 3. Retrieve market data ->
    4. Validate market data -> 5. Detect regime -> 6. Evaluate strategies ->
    7. Identify opportunities -> 8. Calculate EV -> 9. Estimate probability uncertainty ->
    10. Calculate fractional Kelly -> 11. Position size -> 12. Apply survival controller ->
    13. Decide whether to trade -> 14. Submit approved orders -> 15. Verify execution ->
    16. Update portfolio accounting -> 17. Monitor open positions ->
    18-20. Performance, drift, adaptation -> 21. Record decisions -> 22. Repeat.
    """

    def __init__(
        self,
        *,
        exchange: BaseExchange | None = None,
        settings: Settings | None = None,
        registry: StrategyRegistry | None = None,
        survival: SurvivalController | None = None,
        cache: MarketCache | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._custom_exchange = exchange is not None
        self.exchange = exchange or ExchangeFactory.create(self.settings)
        self.registry = registry or StrategyRegistry()
        self.survival = survival or SurvivalController()
        self.cache = cache or MarketCache()
        self.health_monitor = ExchangeHealthMonitor()
        self.collector = MarketDataCollector(self.exchange, cache=self.cache, settings=self.settings)
        self.regime_detector = MarketRegimeDetector()
        self.openbb_research = OpenBBQuantResearch()
        self.selector = StrategySelector(self.registry)
        self.confidence_est = ConfidenceEstimator()
        self.opp_engine = OpportunityEngine(self.confidence_est)
        self.risk_manager = RiskManager(settings=self.settings, survival_controller=self.survival)
        self.execution_engine = ExecutionEngine(
            exchange=self.exchange, survival=self.survival, settings=self.settings
        )
        self.reconciliation = ReconciliationService()
        self.portfolio_manager = PortfolioManager(self.settings)
        self.drift_detector = DriftDetector()
        self.deriv_svc = DerivativesIntelligenceService()

    @staticmethod
    def _is_live_authorized(account: Account) -> bool:
        return bool(
            account.trading_mode == TradingMode.LIVE_TRADING.value
            and account.live_trading_authorized
            and not account.emergency_stopped
        )

    async def run_cycle(
        self,
        session: AsyncSession,
        *,
        timeframe: str = "15m",
        allow_historical: bool = False,
        strategy_stats_override: dict[str, tuple[int, int, int, int]] | None = None,
        manual_trigger: bool = False,
    ) -> dict[str, Any]:
        repo = TradingRepository(session)
        account = await repo.get_or_create_default_account(
            exchange=self.settings.exchange_id,
            trading_mode=self.settings.trading_mode.value,
            initial_capital=self.settings.initial_capital_usd,
            quote_currency=self.settings.quote_currency,
        )

        if not self._custom_exchange:
            prev_exchange = self.exchange
            self.exchange = await ExchangeConnectionService.build_exchange_for_account(
                session, account, settings=self.settings
            )
            self.collector.exchange = self.exchange
            self.execution_engine.exchange = self.exchange
            if prev_exchange is not self.exchange:
                await prev_exchange.close()

        # STEP 1: Check system health (DB + Exchange)
        db_healthy = True
        ex_status = await self.health_monitor.check(self.exchange)
        exchange_healthy = ex_status.is_healthy and not ex_status.circuit_open

        # Sync emergency stop flag with DB account
        if account.emergency_stopped and not self.survival.emergency_stopped:
            self.survival.trigger_emergency_stop("Account emergency_stopped flag active in database.")

        # STEP 2: Sync & reconcile balances against live exchange when connected
        trading_mode = TradingMode(account.trading_mode)
        if (
            not self._custom_exchange
            and trading_mode in {TradingMode.LIVE_TRADING, TradingMode.LIVE_READ_ONLY, TradingMode.TESTNET}
            and await ExchangeConnectionService.get_credential(session, account.id) is not None
        ):
            open_now = await repo.get_open_positions(account.id)
            await ExchangeConnectionService.verify_and_sync_balances(
                session,
                account=account,
                exchange=self.exchange,
                switch_to_read_only_if_paper=False,
                reset_baseline_from_exchange=(len(open_now) == 0 and account.initial_capital <= Decimal("0")),
            )
        recon = await self.reconciliation.reconcile(
            session=session,
            account_id=account.id,
            exchange=self.exchange,
            trading_mode=trading_mode,
        )
        bal_mgr = BalanceManager(session)
        avail_quote = await bal_mgr.get_quote_available(account.id, account.quote_currency)

        latest_prices: dict[str, Decimal] = {}
        decisions_recorded: list[dict[str, Any]] = []
        orders_submitted = 0

        # Enforce strict Futures 1x Isolated policy
        account.market_type = "FUTURES"
        account.leverage = 1

        # STEP 3 & 4: Discover global futures universe (open positions + pinned watchlist + global liquidity & movers)
        open_positions = await repo.get_open_positions(account.id)
        open_set = {p.symbol for p in open_positions}
        pinned = await get_pinned_watchlist(session, self.cache)
        max_buying_power = float(avail_quote * Decimal("5") * Decimal("0.93")) if avail_quote > 0 else None
        global_tickers = await GlobalMarketDiscoveryService.fetch_global_tickers(self.exchange)
        symbols_to_collect = await GlobalMarketDiscoveryService.discover_hot_universe(
            self.exchange,
            base_symbols=self.settings.symbols,
            pinned_symbols=pinned,
            open_symbols=[p.symbol for p in open_positions],
            max_total=64,
            max_buying_power_usd=max_buying_power,
        )
        sem = asyncio.Semaphore(12)

        async def _fetch_charts(syms: list[str], tf: str) -> dict[str, Any]:
            async def _fetch_one(sym: str) -> tuple[str, Any, Exception | None]:
                async with sem:
                    try:
                        bundle = await self.collector.fetch_raw_bundle(
                            sym,
                            timeframe=tf,
                            limit=420 if tf == timeframe else 260,
                            cached_ticker=global_tickers.get(sym),
                            skip_order_book=bool(global_tickers) and (sym not in open_set),
                        )
                        return sym, bundle, None
                    except Exception as exc:
                        return sym, None, exc

            raw_results = await asyncio.gather(*(_fetch_one(sym) for sym in syms))
            collected: dict[str, Any] = {}
            for sym, bundle, exc in raw_results:
                if bundle is None:
                    try:
                        snap, resolved_sym = await GlobalMarketDiscoveryService.collect_symbol_snapshot_any(
                            self.collector,
                            session,
                            sym,
                            timeframe=tf,
                            limit=260,
                            allow_historical=allow_historical,
                            allow_external_feed=(trading_mode != TradingMode.LIVE_TRADING),
                        )
                        collected[resolved_sym] = snap
                        latest_prices[resolved_sym] = snap.ticker.last
                        if resolved_sym != sym:
                            collected[sym] = snap
                            latest_prices[sym] = snap.ticker.last
                    except Exception as inner_exc:
                        await repo.log_agent_event(
                            event_type="MARKET_COLLECTION_FAILED",
                            component="trading_worker",
                            severity="WARNING",
                            message=f"Skipped {sym}: {type(inner_exc).__name__}",
                        )
                    continue
                rules, ticker, order_book, raw_candles = bundle
                snap = await self.collector.persist_raw_bundle(
                    session,
                    sym,
                    tf,
                    rules,
                    ticker,
                    order_book,
                    raw_candles,
                    allow_historical=allow_historical,
                )
                collected[sym] = snap
                latest_prices[sym] = snap.ticker.last
            return collected

        snaps = await _fetch_charts(symbols_to_collect, timeframe)

        # STEP 17: Monitor all open positions with unified ATR scale-out + 1.2x ATR trail + exchange triggers
        now_utc = datetime.now(timezone.utc)
        for pos in open_positions:
            pos_mode = TradingMode(pos.trading_mode) if pos.trading_mode else trading_mode
            trig_fill = await detect_triggered_protective_fill(self.exchange, pos, pos_mode)
            if trig_fill is not None:
                trig_reason, trig_px = trig_fill
                await cancel_protective_orders(self.exchange, pos, pos_mode)
                realized = await self.portfolio_manager.close_position(
                    session,
                    position=pos,
                    exit_price=trig_px,
                    exit_fee=(pos.quantity * trig_px) * Decimal("0.001"),
                    exit_reason=trig_reason,
                )
                avail_quote = await bal_mgr.get_quote_available(account.id, account.quote_currency)
                await repo.log_agent_event(
                    event_type="POSITION_EXIT",
                    component="trading_worker",
                    severity="INFO",
                    message=(
                        f"Exchange trigger filled {pos.side} {pos.symbol} via {trig_reason} @ ${float(trig_px):.4f} "
                        f"(realized PnL: ${float(realized):+.2f})"
                    ),
                    payload={"symbol": pos.symbol, "side": pos.side, "exit_reason": trig_reason, "realized_pnl": float(realized)},
                )
                continue

            snap = snaps.get(pos.symbol)
            if snap is None:
                continue
            is_short = pos.side.upper() == "SHORT"
            exit_px_ref = snap.ticker.ask if is_short else snap.ticker.bid
            exit_side = "BUY" if is_short else "SELL"

            # Track favorable extreme price since entry for 1.2x ATR trailing
            peak_key = f"pos:peak_px:{pos.id}"
            cached_peak = await self.cache.get_json(peak_key)
            cand_hi = Decimal(str(float(snap.candles_df["high"].iloc[-1]))) if len(snap.candles_df) > 0 else exit_px_ref
            cand_lo = Decimal(str(float(snap.candles_df["low"].iloc[-1]))) if len(snap.candles_df) > 0 else exit_px_ref
            curr_extreme = min(exit_px_ref, cand_lo) if is_short else max(exit_px_ref, cand_hi)
            prev_peak_px = Decimal(str(cached_peak["peak_price"])) if cached_peak and cached_peak.get("peak_price") else pos.entry_price
            peak_price = min(prev_peak_px, curr_extreme) if is_short else max(prev_peak_px, curr_extreme)
            await self.cache.set_json(peak_key, {"peak_price": str(peak_price)}, ttl_seconds=14400)

            opened = pos.opened_at if pos.opened_at.tzinfo else pos.opened_at.replace(tzinfo=timezone.utc)
            held_min = (now_utc - opened).total_seconds() / 60.0
            live_atr = extract_atr_value(snap.candles_df) or pos.atr_at_entry or Decimal("0")
            use_micro_exit = (
                not self.settings.paper_enforce_real_exchange_rules
                and pos_mode in {TradingMode.PAPER, TradingMode.BACKTEST}
                and not self._custom_exchange
            )
            exit_rules = snap.rules.for_paper_micro_trading() if use_micro_exit else snap.rules

            decision = evaluate_position_exit_step(
                side=pos.side,
                strategy_name=pos.strategy_name,
                entry_price=pos.entry_price,
                current_price=exit_px_ref,
                stop_loss=pos.stop_loss,
                take_profit=pos.take_profit,
                initial_stop_distance_pct=pos.initial_stop_distance_pct,
                atr_value=live_atr,
                peak_price=peak_price,
                scaled_out=bool(pos.scaled_out),
                held_minutes=held_min,
                settings=self.settings,
            )

            # Update ratcheted breakeven/trail stop loss in DB and on exchange STOP_MARKET
            if decision.new_stop_loss is not None and decision.new_stop_loss != pos.stop_loss:
                pos.stop_loss = decision.new_stop_loss
                if decision.close_fraction < 1.0:
                    await sync_stop_loss_order(
                        self.exchange,
                        pos,
                        decision.new_stop_loss,
                        rules=exit_rules,
                        trading_mode=pos_mode,
                    )

            # Partial scale-out at 1.0R (book 50% and move stop to breakeven+fee)
            if 0.0 < decision.close_fraction < 1.0:
                prec = max(0, int(getattr(exit_rules, "quantity_precision", 6)))
                step = Decimal("10") ** (-prec)
                part_qty = (pos.quantity * Decimal(str(decision.close_fraction))).quantize(step, rounding=ROUND_DOWN)
                rem_qty = pos.quantity - part_qty
                if part_qty > Decimal("0") and rem_qty > Decimal("0"):
                    actual_frac = part_qty / pos.quantity
                    part_margin = (pos.margin_used * actual_frac).quantize(Decimal("0.00000001"))
                    part_fee = (pos.entry_fee * actual_frac).quantize(Decimal("0.00000001"))
                    part_pos = Position(
                        account_id=pos.account_id,
                        symbol=pos.symbol,
                        strategy_name=pos.strategy_name,
                        trading_mode=pos.trading_mode,
                        market_type=pos.market_type,
                        leverage=pos.leverage,
                        margin_used=part_margin,
                        status="OPEN",
                        side=pos.side,
                        quantity=part_qty,
                        entry_price=pos.entry_price,
                        current_price=pos.current_price,
                        stop_loss=pos.stop_loss,
                        take_profit=pos.take_profit,
                        initial_stop_distance_pct=pos.initial_stop_distance_pct,
                        atr_at_entry=pos.atr_at_entry,
                        scaled_out=True,
                        market_regime=pos.market_regime,
                        entry_fee=part_fee,
                        unrealized_pnl=Decimal("0"),
                        realized_pnl=Decimal("0"),
                        opened_at=pos.opened_at,
                    )
                    pos.quantity = rem_qty
                    pos.margin_used = max(Decimal("0"), pos.margin_used - part_margin)
                    pos.entry_fee = max(Decimal("0"), pos.entry_fee - part_fee)
                    pos.scaled_out = True
                    session.add(part_pos)
                    await session.flush()

                    part_req = OrderRequest(
                        symbol=pos.symbol,
                        side=exit_side,
                        order_type="MARKET",
                        quantity=part_qty,
                        client_order_id=OrderManager.build_client_order_id("scale1r"),
                        reduce_only=pos.market_type.upper() == "FUTURES",
                    )
                    part_outcome = await self.execution_engine.execute(
                        session=session,
                        account_id=account.id,
                        risk_decision_id=None,
                        request=part_req,
                        rules=exit_rules,
                        ticker=snap.ticker,
                        order_book=None,
                        trading_mode=pos_mode,
                        live_authorized=(pos_mode == TradingMode.LIVE_TRADING and self._is_live_authorized(account)),
                        strategy_name=pos.strategy_name,
                        market_regime="SCALE_OUT_1R",
                        market_type=pos.market_type,
                        leverage=pos.leverage,
                        closing_position=part_pos,
                    )
                    if part_outcome.executed and part_outcome.result and part_outcome.result.average_price:
                        realized_part = await self.portfolio_manager.close_position(
                            session,
                            position=part_pos,
                            exit_price=part_outcome.result.average_price,
                            exit_fee=part_outcome.result.fee,
                            exit_reason="SCALE_OUT_1R",
                        )
                        if pos.stop_loss is not None:
                            await sync_stop_loss_order(
                                self.exchange,
                                pos,
                                pos.stop_loss,
                                rules=exit_rules,
                                trading_mode=pos_mode,
                            )
                        avail_quote = await bal_mgr.get_quote_available(account.id, account.quote_currency)
                        await repo.log_agent_event(
                            event_type="POSITION_SCALE_OUT_1R",
                            component="trading_worker",
                            severity="INFO",
                            message=(
                                f"Scaled out {float(actual_frac)*100:.0f}% of {pos.side} {pos.symbol} @ "
                                f"${float(part_outcome.result.average_price):.4f} (PnL ${float(realized_part):+.2f}); "
                                f"SL raised to ${float(pos.stop_loss or 0):.4f}"
                            ),
                            payload={
                                "symbol": pos.symbol,
                                "exit_reason": "SCALE_OUT_1R",
                                "realized_pnl": float(realized_part),
                                "new_stop_loss": float(pos.stop_loss or 0),
                            },
                        )
                    else:
                        # Roll back split quantities if partial reduce-only rejected
                        pos.quantity = pos.quantity + part_qty
                        pos.margin_used = pos.margin_used + part_margin
                        pos.entry_fee = pos.entry_fee + part_fee
                        part_pos.status = "CANCELED"
                else:
                    pos.scaled_out = True
                continue

            if decision.close_fraction >= 1.0:
                exit_reason = decision.exit_reason or "EXIT"
                exit_req = OrderRequest(
                    symbol=pos.symbol,
                    side=exit_side,
                    order_type="MARKET",
                    quantity=pos.quantity,
                    client_order_id=OrderManager.build_client_order_id("exit"),
                    reduce_only=pos.market_type.upper() == "FUTURES",
                )
                outcome = await self.execution_engine.execute(
                    session=session,
                    account_id=account.id,
                    risk_decision_id=None,
                    request=exit_req,
                    rules=exit_rules,
                    ticker=snap.ticker,
                    order_book=None,
                    trading_mode=pos_mode,
                    live_authorized=(pos_mode == TradingMode.LIVE_TRADING and self._is_live_authorized(account)),
                    strategy_name=pos.strategy_name,
                    market_regime="POSITION_EXIT",
                    market_type=pos.market_type,
                    leverage=pos.leverage,
                    closing_position=pos,
                )
                if outcome.executed and outcome.result and outcome.result.average_price:
                    await cancel_protective_orders(self.exchange, pos, pos_mode)
                    realized = await self.portfolio_manager.close_position(
                        session,
                        position=pos,
                        exit_price=outcome.result.average_price,
                        exit_fee=outcome.result.fee,
                        exit_reason=exit_reason,
                    )
                    avail_quote = await bal_mgr.get_quote_available(account.id, account.quote_currency)
                    await repo.log_agent_event(
                        event_type="POSITION_EXIT",
                        component="trading_worker",
                        severity="INFO",
                        message=(
                            f"Auto-exited {pos.side} {pos.symbol} via {exit_reason} after "
                            f"{held_min:.1f}m @ ${float(outcome.result.average_price):.4f} "
                            f"(realized PnL: ${float(realized):+.2f})"
                        ),
                        payload={
                            "symbol": pos.symbol,
                            "side": pos.side,
                            "exit_reason": exit_reason,
                            "held_minutes": round(held_min, 2),
                            "realized_pnl": float(realized),
                        },
                    )

        # Recompute open positions after potential exits
        open_positions = await repo.get_open_positions(account.id)
        open_symbols = {p.symbol for p in open_positions}
        current_open_notional = sum((p.quantity * p.current_price for p in open_positions), Decimal("0"))

        # STEP 5-9: Scan ALL discovered coins in the universe & rank setups through Phase 0-2 gates
        recent_trades_cache = await repo.get_recent_trades(account.id, 150)
        live_regime_stats = await repo.get_strategy_regime_realized_stats(account.id)
        should_scan_entries = (
            (account.is_trading_active or manual_trigger)
            and len(open_positions) < self.settings.max_open_positions
            and not account.emergency_stopped
        )

        # Resolve BTC market regime context once per cycle for altcoin correlation filtering
        btc_regime: MarketRegime | None = None
        btc_bearish_slope = False
        btc_bullish_slope = False
        btc_snap = snaps.get("BTC/USDT")
        if btc_snap is not None and len(btc_snap.candles_df) >= 55:
            btc_reg_rep = self.regime_detector.detect(btc_snap.candles_df)
            btc_regime = btc_reg_rep.primary_regime
            from app.strategies.base_strategy import Indicators

            b_close = btc_snap.candles_df["close"].astype(float)
            b_ema20 = float(Indicators.ema(b_close, 20).iloc[-1])
            b_ema50 = float(Indicators.ema(b_close, 50).iloc[-1])
            btc_bearish_slope = b_ema20 < b_ema50 * 0.996 and float(b_close.iloc[-1]) < b_ema20
            btc_bullish_slope = b_ema20 > b_ema50 * 1.004 and float(b_close.iloc[-1]) > b_ema20

        htf_cache: dict[str, Any] = {}
        cycle_bt_cache: dict[tuple[str, str], tuple[int, int, int, int, list[float], list[float]]] = {}

        async def _get_htf_df(sym: str, fallback_df: Any) -> Any:
            if sym in htf_cache:
                return htf_cache[sym]
            try:
                raw_1h = await self.exchange.fetch_ohlcv(sym, "1h", limit=100)
                if raw_1h and len(raw_1h) >= 55:
                    from app.market.candle_manager import CandleManager

                    df_1h = CandleManager.to_dataframe(raw_1h)
                    htf_cache[sym] = df_1h
                    return df_1h
            except Exception:
                pass
            htf_cache[sym] = None
            return None

        async def _evaluate_charts(
            chart_snaps: dict[str, Any], tf: str
        ) -> list[tuple[EvaluatedOpportunity, Any, Any]]:
            found: list[tuple[EvaluatedOpportunity, Any, Any]] = []
            for sym in list(chart_snaps.keys()):
                if sym in open_symbols:
                    continue
                snap = chart_snaps.get(sym)
                if snap is None or not snap.is_tradeable:
                    continue

                regime = self.regime_detector.detect(snap.candles_df)
                openbb_report = self.openbb_research.analyze(snap.candles_df)
                candidates = self.selector.select_for_regime(regime)
                if not candidates:
                    continue

                best_opp: EvaluatedOpportunity | None = None
                deriv_report = None
                htf_df = None
                for strat in candidates:
                    raw_sig = strat.generate_signal(sym, snap.candles_df)
                    sig = normalize_atr_targets(raw_sig, self.settings, df=snap.candles_df)
                    obb_allowed, obb_reason = openbb_report.allows_strategy_entry(strat.name, sig.action)

                    if sig.action not in {"BUY", "SELL"} or not obb_allowed:
                        val_stats = StrategyValidationStats(
                            wins=0,
                            losses=0,
                            oos_wins=0,
                            oos_losses=0,
                            avg_win_pct=float(self.settings.quick_stop_loss_pct) * 1.4,
                            avg_loss_pct=float(self.settings.quick_stop_loss_pct),
                            net_expectancy_pct=0.0,
                            oos_trade_count=0,
                            live_trade_count=0,
                            auto_disabled=False,
                        )
                    elif strategy_stats_override and strat.name in strategy_stats_override:
                        w, l, ow, ol = strategy_stats_override[strat.name]
                        val_stats = StrategyValidationStats(
                            wins=w,
                            losses=l,
                            oos_wins=ow,
                            oos_losses=ol,
                            avg_win_pct=float(self.settings.quick_stop_loss_pct) * 1.4,
                            avg_loss_pct=float(self.settings.quick_stop_loss_pct),
                            net_expectancy_pct=0.0,
                            oos_trade_count=ow + ol,
                            live_trade_count=0,
                            auto_disabled=False,
                        )
                    else:
                        val_stats = await self._validate_strategy_history(
                            strat=strat,
                            primary_symbol=sym,
                            regime_name=regime.primary_regime.value,
                            snaps=chart_snaps,
                            timeframe=tf,
                            live_regime_stats=live_regime_stats,
                            per_symbol_cache=cycle_bt_cache,
                        )

                    if val_stats.auto_disabled:
                        await repo.log_agent_event(
                            event_type="STRATEGY_REGIME_DISABLED",
                            component="trading_worker",
                            severity="WARNING",
                            message=val_stats.disable_reason or f"Auto-disabled {strat.name} in {regime.primary_regime.value}",
                            payload={"strategy": strat.name, "regime": regime.primary_regime.value},
                        )

                    gate_ok = True
                    gate_reason = "HOLD"
                    if sig.action in {"BUY", "SELL"} and obb_allowed and not val_stats.auto_disabled:
                        if deriv_report is None:
                            if (
                                type(self.exchange).__name__ == "MockExchange"
                                and type(self.deriv_svc).fetch_derivatives_sentiment
                                is DerivativesIntelligenceService.fetch_derivatives_sentiment
                            ):
                                from app.intelligence.derivatives_intelligence import DerivativesSentimentReport

                                deriv_report = DerivativesSentimentReport(
                                    symbol=sym,
                                    source="MockOffline",
                                    allow_long=True,
                                    allow_short=True,
                                    rationale="MockExchange offline neutral sentiment.",
                                )
                            else:
                                deriv_report = await self.deriv_svc.fetch_derivatives_sentiment(sym, snap.candles_df)
                        if htf_df is None and sym not in htf_cache:
                            htf_df = await _get_htf_df(sym, snap.candles_df)
                        else:
                            htf_df = htf_cache.get(sym)
                        mr_pair = live_regime_stats.get(("mean_reversion", regime.primary_regime.value)) or live_regime_stats.get(("mean_reversion", "ALL"))
                        mr_exp = float(mr_pair["net_expectancy_pct"]) if mr_pair and int(mr_pair["trade_count"]) >= 10 else 0.0
                        gate_ok, gate_reason = check_phase2_entry_filters(
                            symbol=sym,
                            action=sig.action,
                            strategy_name=strat.name,
                            regime=regime.primary_regime,
                            htf_df=htf_df,
                            btc_regime=btc_regime,
                            btc_bearish_slope=btc_bearish_slope,
                            btc_bullish_slope=btc_bullish_slope,
                            deriv_report=deriv_report,
                            mean_reversion_net_expectancy=mr_exp,
                        )
                    elif val_stats.auto_disabled:
                        gate_ok = False
                        gate_reason = val_stats.disable_reason or "Strategy auto-disabled"

                    sig_row = TradeSignal(
                        strategy_name=sig.strategy,
                        symbol=sig.symbol,
                        timeframe=tf,
                        action=sig.action,
                        market_regime=regime.primary_regime.value,
                        confidence=Decimal(str(sig.confidence)),
                        entry_price=sig.entry_price,
                        stop_loss=sig.stop_loss,
                        take_profit=sig.take_profit,
                        reason=sig.reason,
                        indicators_snapshot={
                            **(sig.indicators or {}),
                            "openbb_quant": openbb_report.to_dict(),
                            "openbb_filter": obb_reason,
                            "phase2_gate": {"passed": gate_ok, "reason": gate_reason},
                            "derivatives_sentiment": deriv_report.to_dict() if deriv_report else None,
                            "oos_validation": {
                                "wins": val_stats.wins,
                                "losses": val_stats.losses,
                                "oos_wins": val_stats.oos_wins,
                                "oos_losses": val_stats.oos_losses,
                                "avg_win_pct": val_stats.avg_win_pct,
                                "avg_loss_pct": val_stats.avg_loss_pct,
                                "net_expectancy_pct": val_stats.net_expectancy_pct,
                                "auto_disabled": val_stats.auto_disabled,
                            },
                        },
                    )
                    session.add(sig_row)
                    await session.flush()

                    if sig.action == "HOLD" or not obb_allowed or not gate_ok:
                        continue

                    prob = self.confidence_est.estimate(
                        wins=val_stats.wins,
                        losses=val_stats.losses,
                        oos_wins=val_stats.oos_wins,
                        oos_losses=val_stats.oos_losses,
                    )
                    avg_w_ovr = None if strategy_stats_override else Decimal(str(val_stats.avg_win_pct))
                    avg_l_ovr = None if strategy_stats_override else Decimal(str(val_stats.avg_loss_pct))
                    ev = self.opp_engine.calculate_expected_value(
                        signal=sig,
                        prob=prob,
                        rules=snap.rules,
                        ticker=snap.ticker,
                        estimated_slippage_bps=snap.liquidity.estimated_slippage_bps,
                        avg_win_override=avg_w_ovr,
                        avg_loss_override=avg_l_ovr,
                    )
                    compat = self.opp_engine.is_regime_compatible(strat.preferred_regimes, regime)
                    deriv_boost = deriv_report.confidence_boost if deriv_report else 0.0
                    opp = EvaluatedOpportunity(
                        signal=sig,
                        regime=regime,
                        probability=prob,
                        ev=ev,
                        regime_compatible=compat,
                        score=(float(ev.net_ev_pct) + deriv_boost * 0.01) * (1.0 if compat else 0.0),
                    )
                    if best_opp is None or (opp.score, opp.signal.confidence) > (
                        best_opp.score,
                        best_opp.signal.confidence,
                    ):
                        best_opp = opp

                if best_opp is not None:
                    found.append((best_opp, regime, snap))
            return found

        ranked_candidates: list[tuple[EvaluatedOpportunity, Any, Any]] = []
        if should_scan_entries:
            ranked_candidates = await _evaluate_charts(snaps, timeframe)
            if not ranked_candidates and manual_trigger and timeframe != "5m" and symbols_to_collect:
                # Fallback scalp pass on 5m charts across top active movers
                scalp_snaps = await _fetch_charts(symbols_to_collect[:24], "5m")
                ranked_candidates = await _evaluate_charts(scalp_snaps, "5m")

        # Rank all ready coins by affordability first (for small wallets), then highest net-EV score + confidence
        wallet_buying_power = avail_quote * Decimal("5") * Decimal("0.93")
        ranked_candidates.sort(
            key=lambda item: (
                1 if item[2].rules.min_notional <= wallet_buying_power else 0,
                item[0].score,
                item[0].signal.confidence,
            ),
            reverse=True,
        )

        executed_trades: list[dict[str, Any]] = []
        last_exec_error: str | None = None

        # STEP 10-15: Size & execute best opportunities up to max_open_positions, refreshing balances after each fill
        for best_opp, regime, snap in ranked_candidates:
            if len(open_positions) >= self.settings.max_open_positions:
                break
            symbol = best_opp.signal.symbol
            # Refresh real live ticker + order book for top actionable candidate before sizing/execution
            if global_tickers:
                try:
                    fresh_ticker = await self.exchange.fetch_ticker(symbol)
                    fresh_ob = await self.exchange.fetch_order_book(symbol, limit=10)
                    snap.ticker = fresh_ticker
                    from app.market.orderbook_manager import OrderBookManager
                    snap.liquidity = OrderBookManager.analyze(fresh_ob)
                    latest_prices[symbol] = fresh_ticker.last
                except Exception:
                    pass

            locked_quote = await bal_mgr.get_quote_locked(account.id, account.quote_currency)
            pos_tuples = [
                (
                    p.quantity,
                    p.entry_price,
                    latest_prices.get(p.symbol, p.current_price),
                    p.entry_fee,
                    p.side,
                    p.market_type,
                    p.margin_used,
                )
                for p in open_positions
            ]
            latest_snap = await repo.get_latest_snapshot(account.id)
            equity_est = EquityCalculator.calculate_mixed(
                initial_capital=account.initial_capital,
                available_quote=avail_quote,
                locked_quote=locked_quote,
                positions=pos_tuples,
                realized_pnl=latest_snap.realized_pnl if latest_snap else Decimal("0"),
                total_fees=Decimal("0"),
            ).total_equity
            sync_utc_day_baseline(account, equity_est)
            use_micro_rules = (
                not self.settings.paper_enforce_real_exchange_rules
                and trading_mode in {TradingMode.PAPER, TradingMode.BACKTEST}
                and not self._custom_exchange
            )
            eff_rules = snap.rules.for_paper_micro_trading() if use_micro_rules else snap.rules
            trade_leverage = effective_isolated_leverage(
                account.leverage, avail_quote, eff_rules, reference_price=snap.ticker.last
            )
            risk_eval = self.risk_manager.evaluate_trade(
                opportunity=best_opp,
                account_equity=equity_est,
                peak_equity=account.peak_equity,
                day_start_equity=account.day_start_equity,
                available_balance=avail_quote,
                open_positions_count=len(open_positions),
                current_open_notional=current_open_notional,
                rules=eff_rules,
                db_healthy=db_healthy,
                exchange_healthy=exchange_healthy,
                market_data_valid=snap.is_tradeable,
                balance_verified=recon.verified,
                market_type="FUTURES",
                leverage=trade_leverage,
                open_positions=open_positions,
            )

            decision_rec = AIAdvisor.build_decision_record(
                opportunity=best_opp,
                kelly_fraction=risk_eval.kelly.fractional_kelly,
                risk_status=risk_eval.decision,
                survival_ok=risk_eval.survival.new_trades_allowed,
                rejection_reasons=risk_eval.rejection_reasons,
            )
            decisions_recorded.append(decision_rec.to_dict())

            rd_row = RiskDecision(
                account_id=account.id,
                symbol=symbol,
                strategy_name=best_opp.signal.strategy,
                market_regime=regime.primary_regime.value,
                survival_state=risk_eval.survival.state.value,
                decision=risk_eval.decision,
                win_probability=Decimal(str(best_opp.probability.conservative_win_probability)),
                probability_lower_bound=Decimal(str(best_opp.probability.ci_low)),
                expected_value=best_opp.ev.net_ev_pct,
                full_kelly=risk_eval.kelly.full_kelly,
                fractional_kelly=risk_eval.kelly.fractional_kelly,
                approved_notional=risk_eval.position_size.capped_notional_usd if risk_eval.approved else Decimal("0"),
                approved_quantity=risk_eval.position_size.quantity if risk_eval.approved else Decimal("0"),
                estimated_fees=risk_eval.position_size.estimated_entry_fee_usd,
                rejection_reasons=risk_eval.rejection_reasons,
                decision_payload=decision_rec.to_dict(),
            )
            session.add(rd_row)
            await session.flush()

            account.survival_state = risk_eval.survival.state.value

            # STEP 14 & 15: Submit approved order & verify execution
            if risk_eval.approved:
                entry_side = best_opp.signal.action.upper()
                order_req = OrderRequest(
                    symbol=symbol,
                    side=entry_side,
                    order_type="MARKET",
                    quantity=risk_eval.position_size.quantity,
                    client_order_id=OrderManager.build_client_order_id(entry_side.lower()),
                )
                outcome = await self.execution_engine.execute(
                    session=session,
                    account_id=account.id,
                    risk_decision_id=rd_row.id,
                    request=order_req,
                    rules=eff_rules,
                    ticker=snap.ticker,
                    order_book=None,
                    trading_mode=trading_mode,
                    live_authorized=self._is_live_authorized(account),
                    strategy_name=best_opp.signal.strategy,
                    market_regime=regime.primary_regime.value,
                    stop_loss=best_opp.signal.stop_loss,
                    take_profit=best_opp.signal.take_profit,
                    market_type="FUTURES",
                    leverage=trade_leverage,
                )
                if outcome.executed and outcome.result:
                    fill_px = (
                        outcome.result.average_price
                        if outcome.result.average_price and outcome.result.average_price > Decimal("0")
                        else (snap.ticker.last if snap.ticker.last > Decimal("0") else best_opp.signal.entry_price)
                    )
                    if outcome.order is not None and outcome.order.average_fill_price is None and fill_px > Decimal("0"):
                        outcome.order.average_fill_price = fill_px
                    orders_submitted += 1
                    pos_side = "SHORT" if entry_side == "SELL" else "LONG"
                    notional = outcome.result.filled_quantity * fill_px
                    margin_used = notional / Decimal(trade_leverage) if fill_px > Decimal("0") else Decimal("0")
                    stop_dist_pct = Decimal(
                        str(
                            (best_opp.signal.indicators or {}).get("initial_stop_distance_pct")
                            or (abs(fill_px - best_opp.signal.stop_loss) / fill_px if fill_px > Decimal("0") else Decimal("0.01"))
                        )
                    )
                    atr_entry = Decimal(str((best_opp.signal.indicators or {}).get("atr") or 0)) or None
                    sl_oid, tp_oid, prot_warnings = await place_protective_orders(
                        self.exchange,
                        symbol=symbol,
                        position_side=pos_side,
                        quantity=outcome.result.filled_quantity,
                        stop_loss=best_opp.signal.stop_loss,
                        take_profit=best_opp.signal.take_profit,
                        rules=eff_rules,
                        trading_mode=trading_mode,
                        market_type="FUTURES",
                    )
                    if prot_warnings:
                        await repo.log_agent_event(
                            event_type="EXCHANGE_PROTECTIVE_ORDER_WARNING",
                            component="trading_worker",
                            severity="WARNING",
                            message="; ".join(prot_warnings),
                            payload={"symbol": symbol, "warnings": prot_warnings},
                        )
                    await self.portfolio_manager.open_position(
                        session,
                        account_id=account.id,
                        symbol=symbol,
                        strategy_name=best_opp.signal.strategy,
                        trading_mode=trading_mode.value,
                        quantity=outcome.result.filled_quantity,
                        entry_price=fill_px,
                        entry_fee=outcome.result.fee,
                        stop_loss=best_opp.signal.stop_loss,
                        take_profit=best_opp.signal.take_profit,
                        side=pos_side,
                        market_type="FUTURES",
                        leverage=trade_leverage,
                        margin_used=margin_used,
                        initial_stop_distance_pct=stop_dist_pct,
                        atr_at_entry=atr_entry,
                        sl_order_id=sl_oid,
                        tp_order_id=tp_oid,
                        market_regime=regime.primary_regime.value,
                    )
                    executed_trades.append(
                        {
                            "symbol": symbol,
                            "side": pos_side,
                            "leverage": trade_leverage,
                            "fill_price": float(fill_px),
                            "notional_usd": float(notional),
                            "margin_usd": float(margin_used),
                            "sl_order_id": sl_oid,
                            "tp_order_id": tp_oid,
                        }
                    )
                    avail_quote = await bal_mgr.get_quote_available(account.id, account.quote_currency)
                    open_positions = await repo.get_open_positions(account.id)
                    open_symbols = {p.symbol for p in open_positions}
                    current_open_notional = sum((p.quantity * p.current_price for p in open_positions), Decimal("0"))
                elif outcome.reason:
                    last_exec_error = outcome.reason

        # STEP 16: Update portfolio accounting & snapshot
        equity_summary, _ = await self.portfolio_manager.mark_to_market(
            session, account=account, latest_prices=latest_prices
        )

        # Refresh survival state when no trade candidates were evaluated so resolved halts clear cleanly
        if not ranked_candidates and not account.emergency_stopped:
            sync_utc_day_baseline(account, equity_summary.total_equity)
            dd_status = self.risk_manager.drawdown_monitor.evaluate(
                current_equity=equity_summary.total_equity,
                peak_equity=max(account.peak_equity, equity_summary.total_equity),
                day_start_equity=account.day_start_equity,
            )
            verdict = self.survival.evaluate(
                drawdown=dd_status,
                db_healthy=db_healthy,
                exchange_healthy=exchange_healthy,
                market_data_valid=bool(snaps),
                balance_verified=recon.verified,
                market_regime=MarketRegime.SIDEWAYS,
            )
            account.survival_state = verdict.state.value

        status_reason: str | None = None
        if orders_submitted == 0:
            top_sig = ranked_candidates[0][0].signal if ranked_candidates else None
            top_preview = f"{top_sig.action} {top_sig.symbol}" if top_sig else None
            if account.emergency_stopped:
                status_reason = "Emergency stop is active — reset recovery first."
            elif trading_mode == TradingMode.LIVE_READ_ONLY:
                status_reason = (
                    f"Account is in LIVE_READ_ONLY safety mode"
                    + (f" (top ready chart: {top_preview})" if top_preview else "")
                    + " — authorize LIVE_TRADING in Settings / API to submit real orders."
                )
            elif trading_mode == TradingMode.LIVE_TRADING and not self._is_live_authorized(account):
                status_reason = "Live execution is not authorized yet — authorize LIVE_TRADING in Settings / API."
            elif last_exec_error:
                status_reason = last_exec_error
            elif decisions_recorded and decisions_recorded[0].get("rejection_reasons"):
                status_reason = f"Checked {len(snaps)} charts; rejected: {'; '.join(decisions_recorded[0]['rejection_reasons'])}"
            elif not should_scan_entries:
                status_reason = (
                    "Max open positions reached"
                    if len(open_positions) >= self.settings.max_open_positions
                    else "Agent is paused — click Start Agent or Scan & Trade Now."
                )
            else:
                status_reason = f"Scanned {len(snaps)} charts ({timeframe}/5m) — waiting for confirmed setup meeting OpenBB + EV + Kelly edge."

        # STEP 21: Record heartbeat & cycle summary
        await self.cache.set_json(
            "worker:trading:heartbeat",
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "survival_state": account.survival_state,
                "equity": str(equity_summary.total_equity),
                "orders_submitted": orders_submitted,
                "status_reason": status_reason,
            },
            ttl_seconds=180,
        )
        return {
            "survival_state": account.survival_state,
            "equity": float(equity_summary.total_equity),
            "orders_submitted": orders_submitted,
            "executed_trades": executed_trades,
            "decisions": decisions_recorded,
            "scanned_symbols": len(snaps),
            "ready_setups": len(ranked_candidates),
            "status_reason": status_reason,
        }

    async def _validate_strategy_history(
        self,
        *,
        strat: Any,
        primary_symbol: str,
        regime_name: str,
        snaps: dict[str, Any],
        timeframe: str,
        live_regime_stats: dict[tuple[str, str], dict[str, Any]],
        per_symbol_cache: dict[tuple[str, str], tuple[int, int, int, int, list[float], list[float]]] | None = None,
    ) -> StrategyValidationStats:
        """
        Run separated historical OOS validation with unified live exit rules & realized payoff metrics.
        Separates historical evaluation slices from the current decision tail and enforces >=50-trade auto-disable.
        """
        ordered_syms = [primary_symbol, *[s for s in snaps.keys() if s != primary_symbol]][:8]
        symbol_frames = [
            (sym, snaps[sym].candles_df)
            for sym in ordered_syms
            if snaps.get(sym) is not None and getattr(snaps[sym], "candles_df", None) is not None and len(snaps[sym].candles_df) >= 120
        ]
        # Skip cache when live regime count reaches auto-disable evaluation territory so fresh closed trades take effect immediately
        live_pair = live_regime_stats.get((strat.name, regime_name.upper())) or live_regime_stats.get((strat.name, "ALL"))
        live_n = int(live_pair["trade_count"]) if live_pair else 0
        cache_key = f"stats:bt_v2:{strat.name}:{primary_symbol}:{regime_name.upper()}:{timeframe}:n{live_n}"
        cached = await self.cache.get_json(cache_key)
        if cached:
            return StrategyValidationStats(**cached)

        stats = await asyncio.to_thread(
            evaluate_separated_oos_stats,
            strategy=strat,
            regime_name=regime_name,
            symbol_frames=symbol_frames,
            live_regime_stats=live_regime_stats,
            settings=self.settings,
            per_symbol_cache=per_symbol_cache,
        )
        await self.cache.set_json(cache_key, stats.__dict__, ttl_seconds=1800)
        return stats


async def main() -> None:
    loop = AutonomousTradingLoop()
    db = get_db_manager()
    try:
        while True:
            async with db.session() as session:
                await loop.run_cycle(session)
            await asyncio.sleep(30)
    finally:
        await loop.exchange.close()
        await loop.cache.close()


if __name__ == "__main__":
    asyncio.run(main())
