"""Trading Worker implementing the 22-step Autonomous Survival-First Operating Loop."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.adaptive.drift_detector import DriftDetector
from app.adaptive.strategy_selector import StrategySelector
from app.backtesting.backtest_engine import BacktestEngine
from app.backtesting.performance_metrics import TradeRecord
from app.config import Settings, TradingMode, get_settings
from app.database.models import Account, RiskDecision, TradeSignal
from app.database.repositories.trading_repo import TradingRepository
from app.database.session import get_db_manager
from app.exchanges.base_exchange import BaseExchange, OrderRequest
from app.exchanges.credentials_service import ExchangeConnectionService
from app.exchanges.exchange_factory import ExchangeFactory
from app.exchanges.exchange_health import ExchangeHealthMonitor
from app.execution.execution_engine import ExecutionEngine
from app.execution.order_manager import OrderManager
from app.execution.reconciliation import ReconciliationService
from app.intelligence.ai_advisor import AIAdvisor
from app.intelligence.confidence_estimator import ConfidenceEstimator
from app.intelligence.openbb_research import OpenBBQuantResearch
from app.intelligence.opportunity_engine import EvaluatedOpportunity, OpportunityEngine
from app.intelligence.regime_detector import MarketRegime, MarketRegimeDetector
from app.market.data_collector import MarketDataCollector
from app.market.market_cache import MarketCache
from app.market.universe_discovery import (
    GlobalMarketDiscoveryService,
    get_pinned_watchlist,
)
from app.portfolio.balance_manager import BalanceManager
from app.portfolio.portfolio_manager import PortfolioManager
from app.risk.position_sizer import effective_isolated_leverage
from app.risk.risk_manager import RiskManager
from app.risk.survival_controller import SurvivalController
from app.strategies.strategy_registry import StrategyRegistry


def clamp_quick_targets(sig: Any, settings: Settings) -> Any:
    """Tighten SL/TP to a short-horizon scalp profile (book profit fast, within ~1 hour)."""
    from dataclasses import replace as dc_replace

    if sig.action not in {"BUY", "SELL"} or not sig.entry_price or sig.entry_price <= 0:
        return sig
    e = sig.entry_price
    tp_d = settings.quick_take_profit_pct
    sl_d = settings.quick_stop_loss_pct
    if sig.action == "BUY":
        min_sl = e * (Decimal("1") - sl_d)
        sl = max(sig.stop_loss, min_sl) if sig.stop_loss and sig.stop_loss < e else min_sl
        sl_dist = e - sl
        max_tp = e * (Decimal("1") + tp_d)
        floor_tp = min(max_tp, e + max(sl_dist * Decimal("2.0"), e * Decimal("0.012")))
        tp = max(floor_tp, min(sig.take_profit, max_tp)) if sig.take_profit and sig.take_profit > e else max_tp
    else:
        max_sl = e * (Decimal("1") + sl_d)
        sl = min(sig.stop_loss, max_sl) if sig.stop_loss and sig.stop_loss > e else max_sl
        sl_dist = sl - e
        min_tp = e * (Decimal("1") - tp_d)
        floor_tp = max(min_tp, e - max(sl_dist * Decimal("2.0"), e * Decimal("0.012")))
        tp = min(floor_tp, max(sig.take_profit, min_tp)) if sig.take_profit and sig.take_profit < e else min_tp
    return dc_replace(sig, take_profit=tp, stop_loss=sl)


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
                            limit=260,
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

        # STEP 17: Monitor all open positions first (SL / TP / trailing peak profit lock / 60-min hard time cap)
        now_utc = datetime.now(timezone.utc)
        for pos in open_positions:
            snap = snaps.get(pos.symbol)
            if snap is None:
                continue
            is_short = pos.side.upper() == "SHORT"
            exit_px_ref = snap.ticker.ask if is_short else snap.ticker.bid
            if is_short:
                hit_sl = pos.stop_loss is not None and snap.ticker.ask >= pos.stop_loss
                hit_tp = pos.take_profit is not None and snap.ticker.ask <= pos.take_profit
                exit_side = "BUY"
                move_pct = (pos.entry_price - exit_px_ref) / pos.entry_price if pos.entry_price > 0 else Decimal("0")
            else:
                hit_sl = pos.stop_loss is not None and snap.ticker.bid <= pos.stop_loss
                hit_tp = pos.take_profit is not None and snap.ticker.bid >= pos.take_profit
                exit_side = "SELL"
                move_pct = (exit_px_ref - pos.entry_price) / pos.entry_price if pos.entry_price > 0 else Decimal("0")

            # Track peak favorable move to capture runners cleanly instead of capping gains prematurely
            peak_key = f"pos:peak:{pos.id}"
            cached_peak = await self.cache.get_json(peak_key)
            prev_peak = Decimal(str(cached_peak["peak_move_pct"])) if cached_peak else move_pct
            peak_move = max(prev_peak, move_pct)
            await self.cache.set_json(peak_key, {"peak_move_pct": str(peak_move)}, ttl_seconds=7200)

            opened = pos.opened_at if pos.opened_at.tzinfo else pos.opened_at.replace(tzinfo=timezone.utc)
            held_min = (now_utc - opened).total_seconds() / 60.0
            hit_time = held_min >= self.settings.max_hold_minutes

            # Arm breakeven+fee trailing profit lock once move crosses fast_book_profit_pct (+0.45%)
            trail_armed = peak_move >= self.settings.fast_book_profit_pct
            if trail_armed and pos.entry_price > 0:
                lock_floor_px = (
                    pos.entry_price * Decimal("0.9975")
                    if is_short
                    else pos.entry_price * Decimal("1.0025")
                )
                if is_short:
                    if pos.stop_loss is None or pos.stop_loss > lock_floor_px:
                        pos.stop_loss = lock_floor_px
                else:
                    if pos.stop_loss is None or pos.stop_loss < lock_floor_px:
                        pos.stop_loss = lock_floor_px

            trail_gap = Decimal("0.0025") if peak_move >= Decimal("0.010") else Decimal("0.0018")
            hit_trailing_lock = (
                trail_armed
                and move_pct <= (peak_move - trail_gap)
                and move_pct >= Decimal("0.0024")
            )
            hit_late_fast_book = trail_armed and held_min >= max(35.0, self.settings.max_hold_minutes * 0.65)
            # After 30 min, take any fee-covering profit instead of stalling
            hit_soft_time = held_min >= self.settings.max_hold_minutes / 2 and move_pct >= Decimal("0.0018")
            if hit_sl or hit_tp or hit_time or hit_trailing_lock or hit_late_fast_book or hit_soft_time:
                exit_reason = (
                    "TAKE_PROFIT"
                    if hit_tp
                    else "TRAILING_PROFIT_LOCK"
                    if (hit_trailing_lock or hit_late_fast_book)
                    else "SOFT_TIME_PROFIT"
                    if hit_soft_time
                    else "TIME_CAP_60M"
                    if hit_time
                    else "STOP_LOSS"
                )
                exit_req = OrderRequest(
                    symbol=pos.symbol,
                    side=exit_side,
                    order_type="MARKET",
                    quantity=pos.quantity,
                    client_order_id=OrderManager.build_client_order_id("exit"),
                    reduce_only=pos.market_type.upper() == "FUTURES",
                )
                pos_mode = TradingMode(pos.trading_mode) if pos.trading_mode else trading_mode
                exit_rules = (
                    snap.rules.for_paper_micro_trading()
                    if (pos_mode in {TradingMode.PAPER, TradingMode.BACKTEST} and not self._custom_exchange)
                    else snap.rules
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
                    realized = await self.portfolio_manager.close_position(
                        session,
                        position=pos,
                        exit_price=outcome.result.average_price,
                        exit_fee=outcome.result.fee,
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

        # STEP 5-9: Scan ALL discovered coins in the universe & rank the strongest setups
        recent_trades_cache = await repo.get_recent_trades(account.id, 150)
        should_scan_entries = (
            (account.is_trading_active or manual_trigger)
            and len(open_positions) < self.settings.max_open_positions
            and not account.emergency_stopped
        )

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
                for strat in candidates:
                    sig = clamp_quick_targets(strat.generate_signal(sym, snap.candles_df), self.settings)
                    obb_allowed, obb_reason = openbb_report.allows_strategy_entry(strat.name, sig.action)
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
                        },
                    )
                    session.add(sig_row)
                    await session.flush()

                    if sig.action == "HOLD" or not obb_allowed:
                        continue

                    if strategy_stats_override and strat.name in strategy_stats_override:
                        w, l, ow, ol = strategy_stats_override[strat.name]
                    else:
                        past_trades = [
                            t
                            for t in recent_trades_cache
                            if t.strategy_name == strat.name and t.realized_pnl is not None
                        ]
                        w = sum(1 for t in past_trades if (t.realized_pnl or 0) > 0)
                        l = sum(1 for t in past_trades if (t.realized_pnl or 0) <= 0)
                        ow, ol = 0, 0
                        if (w + l) < self.confidence_est.min_sample_size:
                            bt_w, bt_l, bt_ow, bt_ol = await self._validate_strategy_history(
                                strat=strat,
                                primary_symbol=sym,
                                snaps=chart_snaps,
                                timeframe=tf,
                            )
                            w += bt_w
                            l += bt_l
                            ow = max(ow, bt_ow)
                            ol = max(ol, bt_ol)

                    prob = self.confidence_est.estimate(wins=w, losses=l, oos_wins=ow, oos_losses=ol)
                    ev = self.opp_engine.calculate_expected_value(
                        signal=sig,
                        prob=prob,
                        rules=snap.rules,
                        ticker=snap.ticker,
                        estimated_slippage_bps=snap.liquidity.estimated_slippage_bps,
                    )
                    compat = self.opp_engine.is_regime_compatible(strat.preferred_regimes, regime)
                    opp = EvaluatedOpportunity(
                        signal=sig,
                        regime=regime,
                        probability=prob,
                        ev=ev,
                        regime_compatible=compat,
                        score=float(ev.net_ev_pct) * (1.0 if compat else 0.0),
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
            equity_est = avail_quote + locked_quote + current_open_notional
            eff_rules = (
                snap.rules.for_paper_micro_trading()
                if (trading_mode in {TradingMode.PAPER, TradingMode.BACKTEST} and not self._custom_exchange)
                else snap.rules
            )
            trade_leverage = effective_isolated_leverage(
                account.leverage, avail_quote, eff_rules, reference_price=snap.ticker.last
            )
            risk_eval = self.risk_manager.evaluate_trade(
                opportunity=best_opp,
                account_equity=equity_est,
                peak_equity=account.peak_equity,
                day_start_equity=account.initial_capital,
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
                if outcome.executed and outcome.result and outcome.result.average_price:
                    orders_submitted += 1
                    pos_side = "SHORT" if entry_side == "SELL" else "LONG"
                    notional = outcome.result.filled_quantity * outcome.result.average_price
                    margin_used = notional / Decimal(trade_leverage)
                    await self.portfolio_manager.open_position(
                        session,
                        account_id=account.id,
                        symbol=symbol,
                        strategy_name=best_opp.signal.strategy,
                        trading_mode=trading_mode.value,
                        quantity=outcome.result.filled_quantity,
                        entry_price=outcome.result.average_price,
                        entry_fee=outcome.result.fee,
                        stop_loss=best_opp.signal.stop_loss,
                        take_profit=best_opp.signal.take_profit,
                        side=pos_side,
                        market_type="FUTURES",
                        leverage=trade_leverage,
                        margin_used=margin_used,
                    )
                    executed_trades.append(
                        {
                            "symbol": symbol,
                            "side": pos_side,
                            "leverage": trade_leverage,
                            "fill_price": float(outcome.result.average_price),
                            "notional_usd": float(notional),
                            "margin_usd": float(margin_used),
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
            dd_status = self.risk_manager.drawdown_monitor.evaluate(
                current_equity=equity_summary.total_equity,
                peak_equity=max(account.peak_equity, equity_summary.total_equity),
                day_start_equity=account.initial_capital,
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
        snaps: dict[str, Any],
        timeframe: str,
    ) -> tuple[int, int, int, int]:
        """Run cached zero-look-ahead historical validation across collected coins to prevent cold-start deadlock."""
        cache_key = f"stats:bt:{strat.name}:{primary_symbol}:{timeframe}"
        cached = await self.cache.get_json(cache_key)
        if cached:
            return (
                int(cached.get("w", 0)),
                int(cached.get("l", 0)),
                int(cached.get("ow", 0)),
                int(cached.get("ol", 0)),
            )

        ordered_syms = [primary_symbol, *[s for s in snaps.keys() if s != primary_symbol]][:8]
        sym_outcomes: dict[str, list[int]] = {}
        missing_syms: list[str] = []
        for sym in ordered_syms:
            sym_cache = await self.cache.get_json(f"stats:bt_sym:{strat.name}:{sym}:{timeframe}")
            if sym_cache and isinstance(sym_cache.get("outcomes"), list):
                sym_outcomes[sym] = [int(v) for v in sym_cache["outcomes"]]
            else:
                snap = snaps.get(sym)
                df = getattr(snap, "candles_df", None)
                if df is not None and len(df) >= 80:
                    missing_syms.append(sym)

        if missing_syms:
            def _compute_missing() -> dict[str, list[int]]:
                engine = BacktestEngine(
                    initial_capital=100.0,
                    fee_rate=0.001,
                    slippage_bps=4.0,
                    spread_bps=3.0,
                    risk_per_trade=float(self.settings.max_risk_per_trade),
                )
                computed: dict[str, list[int]] = {}
                running_total = sum(len(v) for v in sym_outcomes.values())
                for sym in missing_syms:
                    df = snaps[sym].candles_df
                    res = engine.run(strat, sym, df, allow_short=True)
                    outcomes = [1 if tr.pnl > 0 else 0 for tr in res.trades]
                    computed[sym] = outcomes
                    running_total += len(outcomes)
                    if running_total >= 28 and sym != primary_symbol:
                        break
                return computed

            newly_computed = await asyncio.to_thread(_compute_missing)
            for sym, outcomes in newly_computed.items():
                sym_outcomes[sym] = outcomes
                await self.cache.set_json(
                    f"stats:bt_sym:{strat.name}:{sym}:{timeframe}",
                    {"outcomes": outcomes},
                    ttl_seconds=1800,
                )

        wins_all: list[int] = []
        for sym in ordered_syms:
            if sym in sym_outcomes:
                wins_all.extend(sym_outcomes[sym])
            if len(wins_all) >= 28 and sym != primary_symbol:
                break

        if not wins_all:
            w, l, ow, ol = 0, 0, 0, 0
        else:
            split = max(1, int(len(wins_all) * 0.7))
            is_slice = wins_all[:split]
            oos_slice = wins_all[split:] or wins_all[-min(6, len(wins_all)):]
            w = sum(is_slice)
            l = len(is_slice) - w
            ow = sum(oos_slice)
            ol = len(oos_slice) - ow

        await self.cache.set_json(cache_key, {"w": w, "l": l, "ow": ow, "ol": ol}, ttl_seconds=1800)
        return w, l, ow, ol


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
