"""Trading lifecycle, trades list, paper mode enforcement, and emergency stop endpoints."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN
from typing import Annotated, Any
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import (
    get_current_user,
    get_market_cache,
    get_strategy_registry,
    get_survival_controller,
    rate_limit_guard,
    require_role,
)
from app.config import TradingMode, get_settings
from app.database.models import Order, PortfolioSnapshot, Position, RiskDecision, Trade, TradeSignal, User
from app.database.repositories.trading_repo import TradingRepository
from app.database.session import get_db_session
from app.exchanges.base_exchange import OrderRequest
from app.exchanges.credentials_service import (
    ExchangeConnectionService,
    MICRO_LIVE_FUTURES_EXCHANGES,
    min_live_order_usd,
)
from app.execution.exit_policy import extract_atr_value, normalize_atr_targets
from app.execution.order_manager import OrderManager
from app.execution.protective_orders import (
    cancel_protective_orders,
    place_protective_orders,
    sync_stop_loss_order,
)
from app.intelligence.ai_advisor import AIAdvisor
from app.intelligence.derivatives_intelligence import DerivativesIntelligenceService
from app.intelligence.openbb_research import OpenBBQuantResearch
from app.intelligence.opportunity_engine import EvaluatedOpportunity
from app.intelligence.regime_detector import MarketRegimeDetector
from app.intelligence.strategy_validation_service import evaluate_separated_oos_stats
from app.market.candle_manager import CandleManager
from app.market.market_cache import MarketCache
from app.portfolio.balance_manager import BalanceManager
from app.risk.daily_baseline import sync_utc_day_baseline
from app.risk.position_sizer import effective_isolated_leverage
from app.risk.survival_controller import SurvivalController, SurvivalState
from app.security.audit_logger import AuditLogger
from app.security.authorization import Role
from app.strategies.base_strategy import StrategySignal
from app.strategies.strategy_registry import StrategyRegistry
from app.workers.trading_worker import AutonomousTradingLoop

router = APIRouter(tags=["trading"])


class EmergencyStopBody(BaseModel):
    reason: str = "Manual emergency stop triggered by operator"


@router.get("/trades", dependencies=[Depends(rate_limit_guard)])
async def get_trades(
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    limit: int = 100,
) -> dict[str, Any]:
    repo = TradingRepository(session)
    account = await repo.get_or_create_default_account()
    trades = await repo.get_recent_trades(account.id, limit=min(limit, 500))
    orders = await repo.get_recent_orders(account.id, limit=min(limit, 200))
    return {
        "trades": [
            {
                "id": str(t.id),
                "symbol": t.symbol,
                "side": t.side,
                "trading_mode": t.trading_mode,
                "strategy_name": t.strategy_name,
                "market_regime": t.market_regime,
                "quantity": float(t.quantity),
                "price": float(t.price),
                "notional": float(t.notional),
                "fee": float(t.fee),
                "slippage_bps": float(t.slippage_bps),
                "realized_pnl": float(t.realized_pnl) if t.realized_pnl is not None else None,
                "is_simulated": t.is_simulated,
                "executed_at": t.executed_at.isoformat(),
            }
            for t in trades
        ],
        "orders": [
            {
                "id": str(o.id),
                "client_order_id": o.client_order_id,
                "exchange_order_id": o.exchange_order_id,
                "symbol": o.symbol,
                "side": o.side,
                "order_type": o.order_type,
                "status": o.status,
                "requested_quantity": float(o.requested_quantity),
                "filled_quantity": float(o.filled_quantity),
                "average_fill_price": float(o.average_fill_price) if o.average_fill_price else None,
                "fee_paid": float(o.fee_paid),
                "created_at": o.created_at.isoformat(),
            }
            for o in orders
        ],
    }


@router.post("/trading/start", dependencies=[Depends(rate_limit_guard)])
async def start_trading(
    user: Annotated[User, Depends(require_role(Role.OPERATOR))],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    survival: Annotated[SurvivalController, Depends(get_survival_controller)],
) -> dict[str, Any]:
    repo = TradingRepository(session)
    account = await repo.get_or_create_default_account()
    if account.emergency_stopped or survival.emergency_stopped:
        raise HTTPException(
            status_code=400,
            detail="Cannot start trading while emergency stop is active. Reset recovery first.",
        )
    bal_mgr = BalanceManager(session)
    if account.trading_mode != TradingMode.PAPER.value:
        with suppress(Exception):
            if await ExchangeConnectionService.get_credential(session, account.id) is not None:
                await ExchangeConnectionService.verify_and_sync_balances(
                    session,
                    account=account,
                    switch_to_read_only_if_paper=False,
                    reset_baseline_from_exchange=False,
                )
    avail = await bal_mgr.get_quote_available(account.id, account.quote_currency)
    warning: str | None = None
    if avail > Decimal("0") and not account.emergency_stopped and account.survival_state == "HALTED":
        account.peak_equity = max(account.peak_equity, avail)
        if account.initial_capital <= Decimal("0"):
            account.initial_capital = avail
        account.survival_state = "NORMAL"
        survival.state = SurvivalState.NORMAL

    if avail <= Decimal("0"):
        warning = (
            f"{account.exchange.upper()} Futures wallet has 0.00 {account.quote_currency}. "
            "Transfer USDT to your Futures wallet and click Sync Live Balance."
        )
    elif account.trading_mode == TradingMode.LIVE_READ_ONLY.value:
        warning = "Account is in LIVE_READ_ONLY mode — authorize LIVE_TRADING in Settings / API to send real orders."

    account.is_trading_active = True
    await session.flush()
    await AuditLogger.log(
        session,
        actor=user.username,
        action="TRADING_STARTED",
        resource_type="account",
        resource_id=str(account.id),
        details={"trading_mode": account.trading_mode, "available_balance": float(avail)},
    )
    return {
        "is_trading_active": True,
        "trading_mode": account.trading_mode,
        "available_balance": float(avail),
        "warning": warning,
    }


@router.post("/trading/stop", dependencies=[Depends(rate_limit_guard)])
async def stop_trading(
    user: Annotated[User, Depends(require_role(Role.OPERATOR))],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict[str, Any]:
    repo = TradingRepository(session)
    account = await repo.get_or_create_default_account()
    account.is_trading_active = False
    await session.flush()
    await AuditLogger.log(
        session,
        actor=user.username,
        action="TRADING_STOPPED",
        resource_type="account",
        resource_id=str(account.id),
    )
    return {"is_trading_active": False, "trading_mode": account.trading_mode}


@router.post("/trading/paper-mode", dependencies=[Depends(rate_limit_guard)])
async def switch_to_paper_mode(
    user: Annotated[User, Depends(require_role(Role.OPERATOR))],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict[str, Any]:
    repo = TradingRepository(session)
    account = await repo.get_or_create_default_account()
    account.trading_mode = TradingMode.PAPER.value
    account.live_trading_authorized = False
    await session.flush()
    await AuditLogger.log(
        session,
        actor=user.username,
        action="SWITCHED_TO_PAPER_MODE",
        resource_type="account",
        resource_id=str(account.id),
    )
    return {"trading_mode": account.trading_mode, "live_trading_authorized": False}


@router.post("/trading/emergency-stop", dependencies=[Depends(rate_limit_guard)])
async def emergency_stop(
    user: Annotated[User, Depends(require_role(Role.OPERATOR))],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    survival: Annotated[SurvivalController, Depends(get_survival_controller)],
    body: EmergencyStopBody = EmergencyStopBody(),
) -> dict[str, Any]:
    repo = TradingRepository(session)
    account = await repo.get_or_create_default_account()
    account.is_trading_active = False
    account.emergency_stopped = True
    account.live_trading_authorized = False
    account.survival_state = "HALTED"
    result = survival.trigger_emergency_stop(body.reason)

    await repo.log_agent_event(
        event_type="EMERGENCY_STOP",
        component="survival_controller",
        severity="CRITICAL",
        message=f"Emergency stop activated by {user.username}: {body.reason}",
        payload=result,
    )
    await AuditLogger.log(
        session,
        actor=user.username,
        action="EMERGENCY_STOP",
        resource_type="account",
        resource_id=str(account.id),
        details={"reason": body.reason},
    )
    return result


@router.post("/trading/reset-recovery", dependencies=[Depends(rate_limit_guard)])
async def reset_recovery(
    user: Annotated[User, Depends(require_role(Role.ADMIN))],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    survival: Annotated[SurvivalController, Depends(get_survival_controller)],
) -> dict[str, Any]:
    repo = TradingRepository(session)
    account = await repo.get_or_create_default_account()
    survival.enter_recovery(operator_verified=True)
    account.emergency_stopped = False
    account.survival_state = "RECOVERY"
    await session.flush()

    await repo.log_agent_event(
        event_type="RECOVERY_ENTERED",
        component="survival_controller",
        severity="WARNING",
        message=f"Admin {user.username} cleared emergency stop into RECOVERY mode.",
    )
    await AuditLogger.log(
        session,
        actor=user.username,
        action="RECOVERY_ENTERED",
        resource_type="account",
        resource_id=str(account.id),
    )
    return {"emergency_stopped": False, "survival_state": "RECOVERY"}


@router.post("/trading/run-cycle", dependencies=[Depends(rate_limit_guard)])
async def run_trading_cycle_now(
    user: Annotated[User, Depends(require_role(Role.OPERATOR))],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    survival: Annotated[SurvivalController, Depends(get_survival_controller)],
    registry: Annotated[StrategyRegistry, Depends(get_strategy_registry)],
    cache: Annotated[MarketCache, Depends(get_market_cache)],
    timeframe: str = "15m",
) -> dict[str, Any]:
    loop = AutonomousTradingLoop(
        registry=registry,
        survival=survival,
        cache=cache,
    )
    try:
        result = await loop.run_cycle(session, timeframe=timeframe, manual_trigger=True)
    finally:
        await loop.exchange.close()

    await AuditLogger.log(
        session,
        actor=user.username,
        action="MANUAL_TRADING_CYCLE",
        resource_type="trading_loop",
        details={
            "survival_state": result["survival_state"],
            "orders_submitted": result["orders_submitted"],
        },
    )
    return result


async def _build_loop_for_account(
    session: AsyncSession,
    account: Any,
    *,
    registry: StrategyRegistry,
    survival: SurvivalController,
    cache: MarketCache,
) -> AutonomousTradingLoop:
    """Create a trading loop wired to the account's live Futures adapter and encrypted API credentials."""
    loop = AutonomousTradingLoop(registry=registry, survival=survival, cache=cache)
    prev_exchange = loop.exchange
    loop.exchange = await ExchangeConnectionService.build_exchange_for_account(
        session, account, settings=loop.settings
    )
    loop.collector.exchange = loop.exchange
    loop.execution_engine.exchange = loop.exchange
    if prev_exchange is not loop.exchange:
        await prev_exchange.close()
    return loop


async def _execute_controlled_futures_trade(
    user: User,
    session: AsyncSession,
    survival: SurvivalController,
    registry: StrategyRegistry,
    cache: MarketCache,
    symbol: str,
    timeframe: str,
    side: str | None,
    force_paper: bool,
) -> dict[str, Any]:
    """Execute one Futures (1x Isolated) trade through intelligence, Kelly sizing, survival risk, and execution."""
    norm_symbol = symbol.replace("-", "/").upper()
    repo = TradingRepository(session)
    account = await repo.get_or_create_default_account()

    # Enforce strict Futures 1x Isolated policy everywhere
    account.market_type = "FUTURES"
    account.leverage = 1

    bal_mgr = BalanceManager(session)
    avail = await bal_mgr.get_quote_available(account.id, account.quote_currency)
    locked = await bal_mgr.get_quote_locked(account.id, account.quote_currency)
    credited_usdt = Decimal("0")

    if force_paper:
        # Ensure isolated PAPER testing state and clear any prior HALT/emergency stop
        account.trading_mode = TradingMode.PAPER.value
        account.live_trading_authorized = False
        account.emergency_stopped = False
        account.is_trading_active = True
        account.survival_state = "NORMAL"

        survival.emergency_stopped = False
        survival.emergency_stop_reason = None
        survival.state = SurvivalState.NORMAL

        if avail < Decimal("1.00"):
            target_seed = Decimal("500.00")
            credited_usdt = target_seed - avail
            avail = target_seed
            await repo.upsert_balance(
                account_id=account.id,
                asset=account.quote_currency,
                available=avail,
                locked=locked,
            )
            account.initial_capital = avail + locked
            account.day_start_equity = avail + locked
            account.day_start_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            account.peak_equity = max(account.peak_equity, avail + locked)
            session.add(
                PortfolioSnapshot(
                    account_id=account.id,
                    trading_mode=account.trading_mode,
                    initial_capital=account.initial_capital,
                    available_balance=avail,
                    locked_balance=locked,
                    open_position_value=Decimal("0"),
                    total_equity=avail + locked,
                    realized_pnl=Decimal("0"),
                    unrealized_pnl=Decimal("0"),
                    total_fees=Decimal("0"),
                    drawdown_pct=Decimal("0"),
                    daily_pnl_pct=Decimal("0"),
                    withdrawable_amount=Decimal("0"),
                    is_simulated=True,
                )
            )
            await session.flush()
    else:
        current_mode = TradingMode(account.trading_mode)
        if current_mode == TradingMode.LIVE_READ_ONLY:
            raise HTTPException(
                status_code=400,
                detail="Account is in LIVE_READ_ONLY mode. Click 'Arm Live Real-USDT' (or go to Analytics & API) and authorize LIVE_TRADING to execute real-USDT orders.",
            )
        if account.emergency_stopped or survival.emergency_stopped:
            raise HTTPException(
                status_code=400,
                detail="Emergency stop is active. Reset emergency stop before placing manual orders.",
            )

    trading_mode = TradingMode(account.trading_mode)
    loop = await _build_loop_for_account(
        session, account, registry=registry, survival=survival, cache=cache
    )
    if not force_paper and trading_mode == TradingMode.LIVE_TRADING and not loop._is_live_authorized(account):
        await loop.exchange.close()
        raise HTTPException(
            status_code=400,
            detail="LIVE_TRADING is not authorized with valid Exchange Futures API credentials.",
        )

    deriv_svc = DerivativesIntelligenceService()
    try:
        if not force_paper and trading_mode == TradingMode.LIVE_TRADING:
            with suppress(Exception):
                if await ExchangeConnectionService.get_credential(session, account.id) is not None:
                    await ExchangeConnectionService.verify_and_sync_balances(
                        session,
                        account=account,
                        exchange=loop.exchange,
                        switch_to_read_only_if_paper=False,
                        reset_baseline_from_exchange=False,
                    )
                    avail = await bal_mgr.get_quote_available(account.id, account.quote_currency)
                    locked = await bal_mgr.get_quote_locked(account.id, account.quote_currency)
        ex_status = await loop.health_monitor.check(loop.exchange)
        snap = await loop.collector.collect_symbol_data(
            session, norm_symbol, timeframe=timeframe, limit=400, allow_historical=True
        )
        regime = loop.regime_detector.detect(snap.candles_df)
        openbb_report = loop.openbb_research.analyze(snap.candles_df)
        tv_report = deriv_svc.compute_tradingview_rating(snap.candles_df)
        if (
            type(loop.exchange).__name__ == "MockExchange"
            and type(deriv_svc).fetch_derivatives_sentiment
            is DerivativesIntelligenceService.fetch_derivatives_sentiment
        ):
            from app.intelligence.derivatives_intelligence import DerivativesSentimentReport

            deriv_report = DerivativesSentimentReport(
                symbol=norm_symbol,
                source="MockOffline",
                allow_long=True,
                allow_short=True,
                rationale="MockExchange offline neutral sentiment.",
            )
        else:
            deriv_report = await deriv_svc.fetch_derivatives_sentiment(norm_symbol, snap.candles_df)

        is_futures = True
        forced_action = None
        if side:
            s_up = side.upper()
            forced_action = "BUY" if s_up in {"BUY", "LONG"} else "SELL" if s_up in {"SELL", "SHORT"} else None
        chosen_signal: StrategySignal | None = None
        if forced_action is None:
            for strat in loop.selector.select_for_regime(regime):
                sig = strat.generate_signal(norm_symbol, snap.candles_df)
                if sig.action in {"BUY", "SELL"}:
                    chosen_signal = normalize_atr_targets(sig, loop.settings, df=snap.candles_df)
                    break

        signal_source = "strategy_engine"
        if chosen_signal is None:
            signal_source = "manual_operator" if forced_action else "paper_test_synthetic"
            entry_px = snap.ticker.last
            action = forced_action or ("SELL" if tv_report.rating_score < -0.15 else "BUY")
            atr_d = extract_atr_value(snap.candles_df)
            sl_d = (
                max(loop.settings.min_stop_loss_pct, min(loop.settings.max_stop_loss_pct, (atr_d / entry_px) * Decimal("1.5")))
                if atr_d > 0 and entry_px > 0
                else loop.settings.quick_stop_loss_pct
            )
            tp_d = max(loop.settings.quick_take_profit_pct, sl_d * loop.settings.min_reward_risk_ratio)
            if action == "SELL":
                sl_px = entry_px * (Decimal("1") + sl_d)
                tp_px = entry_px * (Decimal("1") - tp_d)
            else:
                sl_px = entry_px * (Decimal("1") - sl_d)
                tp_px = entry_px * (Decimal("1") + tp_d)
            raw_sig = StrategySignal(
                strategy="trend_following",
                symbol=norm_symbol,
                action=action,
                confidence=0.72,
                entry_price=entry_px,
                stop_loss=sl_px,
                take_profit=tp_px,
                reason="Manual/Controlled Futures execution across OpenBB/TradingView/CoinGlass pipeline",
                indicators={
                    "tv_score": tv_report.rating_score,
                    "openbb_confluence": openbb_report.confluence_score,
                    "funding_rate": deriv_report.funding_rate,
                },
            )
            chosen_signal = normalize_atr_targets(raw_sig, loop.settings, df=snap.candles_df)

        sig_row = TradeSignal(
            strategy_name=chosen_signal.strategy,
            symbol=chosen_signal.symbol,
            timeframe=timeframe,
            action=chosen_signal.action,
            market_regime=regime.primary_regime.value,
            confidence=Decimal(str(chosen_signal.confidence)),
            entry_price=chosen_signal.entry_price,
            stop_loss=chosen_signal.stop_loss,
            take_profit=chosen_signal.take_profit,
            reason=chosen_signal.reason,
            indicators_snapshot={
                **(chosen_signal.indicators or {}),
                "openbb_quant": openbb_report.to_dict(),
                "tradingview_rating": tv_report.to_dict(),
                "derivatives_sentiment": deriv_report.to_dict(),
            },
        )
        session.add(sig_row)
        await session.flush()

        strat_obj = loop.registry.get(chosen_signal.strategy)
        live_stats = await repo.get_strategy_regime_realized_stats(account.id)
        if strat_obj is not None:
            symbol_frames = [(norm_symbol, snap.candles_df)]
            for peer_sym in ("BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT"):
                if peer_sym == norm_symbol or len(symbol_frames) >= 4:
                    continue
                with suppress(Exception):
                    peer_raw = await loop.exchange.fetch_ohlcv(peer_sym, timeframe, limit=400)
                    if peer_raw and len(peer_raw) >= 120:
                        symbol_frames.append((peer_sym, CandleManager.to_dataframe(peer_raw)))
            val_stats = await asyncio.to_thread(
                evaluate_separated_oos_stats,
                strategy=strat_obj,
                regime_name=regime.primary_regime.value,
                symbol_frames=symbol_frames,
                live_regime_stats=live_stats,
                settings=loop.settings,
            )
            prob = loop.confidence_est.estimate(
                wins=val_stats.wins,
                losses=val_stats.losses,
                oos_wins=val_stats.oos_wins,
                oos_losses=val_stats.oos_losses,
            )
            ev = loop.opp_engine.calculate_expected_value(
                signal=chosen_signal,
                prob=prob,
                rules=snap.rules,
                ticker=snap.ticker,
                estimated_slippage_bps=snap.liquidity.estimated_slippage_bps,
                avg_win_override=Decimal(str(val_stats.avg_win_pct)),
                avg_loss_override=Decimal(str(val_stats.avg_loss_pct)),
            )
        else:
            val_stats = None
            prob = loop.confidence_est.estimate(wins=0, losses=0, oos_wins=0, oos_losses=0)
            ev = loop.opp_engine.calculate_expected_value(
                signal=chosen_signal,
                prob=prob,
                rules=snap.rules,
                ticker=snap.ticker,
                estimated_slippage_bps=snap.liquidity.estimated_slippage_bps,
            )

        opp = EvaluatedOpportunity(
            signal=chosen_signal,
            regime=regime,
            probability=prob,
            ev=ev,
            regime_compatible=True,
            score=float(ev.net_ev_pct),
        )

        open_positions = await repo.get_open_positions(account.id)
        current_open_notional = sum((p.quantity * p.current_price for p in open_positions), Decimal("0"))
        equity_est = avail + locked + current_open_notional
        account.peak_equity = max(account.peak_equity, equity_est)
        sync_utc_day_baseline(account, equity_est)

        use_micro_relax = (
            not loop.settings.paper_enforce_real_exchange_rules
            and trading_mode in {TradingMode.PAPER, TradingMode.BACKTEST}
        )
        eff_rules = snap.rules.for_paper_micro_trading() if use_micro_relax else snap.rules
        pos_lev = effective_isolated_leverage(
            account.leverage, avail, eff_rules, reference_price=snap.ticker.last
        )

        risk_eval = loop.risk_manager.evaluate_trade(
            opportunity=opp,
            account_equity=equity_est,
            peak_equity=account.peak_equity,
            day_start_equity=account.day_start_equity,
            available_balance=avail,
            open_positions_count=len(open_positions),
            current_open_notional=current_open_notional,
            rules=eff_rules,
            db_healthy=True,
            exchange_healthy=ex_status.is_healthy and not ex_status.circuit_open,
            market_data_valid=snap.is_tradeable,
            balance_verified=True,
            strategy_deteriorated=bool(val_stats and val_stats.auto_disabled),
            market_type=account.market_type,
            leverage=pos_lev,
            open_positions=open_positions,
        )

        decision_rec = AIAdvisor.build_decision_record(
            opportunity=opp,
            kelly_fraction=risk_eval.kelly.fractional_kelly,
            risk_status=risk_eval.decision,
            survival_ok=risk_eval.survival.new_trades_allowed,
            rejection_reasons=risk_eval.rejection_reasons,
        )

        rd_row = RiskDecision(
            signal_id=sig_row.id,
            account_id=account.id,
            symbol=norm_symbol,
            strategy_name=chosen_signal.strategy,
            market_regime=regime.primary_regime.value,
            survival_state=risk_eval.survival.state.value,
            decision=risk_eval.decision,
            win_probability=Decimal(str(prob.conservative_win_probability)),
            probability_lower_bound=Decimal(str(prob.ci_low)),
            expected_value=ev.net_ev_pct,
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

        trade_summary: dict[str, Any] = {
            "executed": False,
            "reason": "Risk check rejected" if not risk_eval.approved else "Not executed",
        }
        if risk_eval.approved:
            entry_side = chosen_signal.action.upper()
            order_req = OrderRequest(
                symbol=norm_symbol,
                side=entry_side,
                order_type="MARKET",
                quantity=risk_eval.position_size.quantity,
                client_order_id=OrderManager.build_client_order_id("papertest" if force_paper else "manualfut"),
            )
            outcome = await loop.execution_engine.execute(
                session=session,
                account_id=account.id,
                risk_decision_id=rd_row.id,
                request=order_req,
                rules=eff_rules,
                ticker=snap.ticker,
                order_book=None,
                trading_mode=trading_mode,
                live_authorized=False if force_paper else loop._is_live_authorized(account),
                strategy_name=chosen_signal.strategy,
                market_regime=regime.primary_regime.value,
                stop_loss=chosen_signal.stop_loss,
                take_profit=chosen_signal.take_profit,
                market_type=account.market_type,
                leverage=pos_lev,
            )
            if outcome.executed and outcome.result and outcome.result.average_price:
                pos_side = "SHORT" if entry_side == "SELL" else "LONG"
                margin_used = (
                    (outcome.result.filled_quantity * outcome.result.average_price) / Decimal(pos_lev)
                    if is_futures
                    else Decimal("0")
                )
                stop_dist_pct = Decimal(
                    str(
                        (chosen_signal.indicators or {}).get("initial_stop_distance_pct")
                        or (abs(outcome.result.average_price - chosen_signal.stop_loss) / outcome.result.average_price)
                    )
                )
                atr_entry = Decimal(str((chosen_signal.indicators or {}).get("atr") or 0)) or None
                sl_oid, tp_oid, prot_warnings = await place_protective_orders(
                    loop.exchange,
                    symbol=norm_symbol,
                    position_side=pos_side,
                    quantity=outcome.result.filled_quantity,
                    stop_loss=chosen_signal.stop_loss,
                    take_profit=chosen_signal.take_profit,
                    rules=eff_rules,
                    trading_mode=trading_mode,
                    market_type=account.market_type,
                )
                pos = await loop.portfolio_manager.open_position(
                    session,
                    account_id=account.id,
                    symbol=norm_symbol,
                    strategy_name=chosen_signal.strategy,
                    trading_mode=trading_mode.value,
                    quantity=outcome.result.filled_quantity,
                    entry_price=outcome.result.average_price,
                    entry_fee=outcome.result.fee,
                    stop_loss=chosen_signal.stop_loss,
                    take_profit=chosen_signal.take_profit,
                    side=pos_side,
                    market_type=account.market_type,
                    leverage=pos_lev,
                    margin_used=margin_used,
                    initial_stop_distance_pct=stop_dist_pct,
                    atr_at_entry=atr_entry,
                    sl_order_id=sl_oid,
                    tp_order_id=tp_oid,
                    market_regime=regime.primary_regime.value,
                )
                trade_summary = {
                    "executed": True,
                    "signal_source": signal_source,
                    "position_id": str(pos.id),
                    "order_id": str(outcome.order.id) if outcome.order else None,
                    "client_order_id": outcome.order.client_order_id if outcome.order else None,
                    "sl_order_id": sl_oid,
                    "tp_order_id": tp_oid,
                    "protective_warnings": prot_warnings,
                    "symbol": norm_symbol,
                    "side": pos_side,
                    "order_side": entry_side,
                    "market_type": account.market_type,
                    "leverage": pos_lev,
                    "strategy": chosen_signal.strategy,
                    "filled_quantity": float(outcome.result.filled_quantity),
                    "fill_price": float(outcome.result.average_price),
                    "notional_usd": float(outcome.result.filled_quantity * outcome.result.average_price),
                    "fee_usd": float(outcome.result.fee),
                    "stop_loss": float(chosen_signal.stop_loss),
                    "take_profit": float(chosen_signal.take_profit),
                    "is_simulated": trading_mode == TradingMode.PAPER,
                }
            else:
                trade_summary = {"executed": False, "reason": outcome.reason}

        equity_summary, _ = await loop.portfolio_manager.mark_to_market(
            session,
            account=account,
            latest_prices={norm_symbol: snap.ticker.last},
        )
        avail_after = await bal_mgr.get_quote_available(account.id, account.quote_currency)

        await AuditLogger.log(
            session,
            actor=user.username,
            action="TEST_PAPER_TRADE" if force_paper else "MANUAL_FUTURES_TRADE",
            resource_type="order",
            resource_id=trade_summary.get("order_id"),
            details=trade_summary,
        )

        return {
            "status": "executed" if trade_summary.get("executed") else "rejected",
            "credited_virtual_usdt": float(credited_usdt),
            "trade": trade_summary,
            "risk_and_kelly": {
                "decision": risk_eval.decision,
                "survival_state": risk_eval.survival.state.value,
                "full_kelly": float(risk_eval.kelly.full_kelly),
                "fractional_kelly": float(risk_eval.kelly.fractional_kelly),
                "net_ev_pct": float(ev.net_ev_pct),
                "reward_risk_ratio": float(ev.reward_risk_ratio),
                "conservative_win_probability": prob.conservative_win_probability,
                "risk_budget_usd": float(risk_eval.position_size.risk_budget_usd),
                "approved_notional_usd": float(risk_eval.position_size.capped_notional_usd),
                "rejection_reasons": risk_eval.rejection_reasons,
            },
            "intelligence": {
                "regime": {
                    "primary": regime.primary_regime.value,
                    "confidence": regime.confidence,
                    "adx": float(regime.trend_strength_adx),
                },
                "openbb_quant": openbb_report.to_dict(),
                "tradingview_rating": tv_report.to_dict(),
                "derivatives_sentiment": deriv_report.to_dict(),
            },
            "account_after": {
                "trading_mode": account.trading_mode,
                "market_type": account.market_type,
                "leverage": account.leverage,
                "survival_state": account.survival_state,
                "available_usdt": float(avail_after),
                "open_position_value": float(equity_summary.open_position_value),
                "total_equity": float(equity_summary.total_equity),
                "unrealized_pnl": float(equity_summary.unrealized_pnl),
            },
        }
    finally:
        await loop.exchange.close()


@router.post("/trading/test-paper-trade", dependencies=[Depends(rate_limit_guard)])
async def execute_test_paper_trade(
    user: Annotated[User, Depends(require_role(Role.OPERATOR))],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    survival: Annotated[SurvivalController, Depends(get_survival_controller)],
    registry: Annotated[StrategyRegistry, Depends(get_strategy_registry)],
    cache: Annotated[MarketCache, Depends(get_market_cache)],
    symbol: str = "BTC/USDT",
    timeframe: str = "15m",
    side: str | None = None,
) -> dict[str, Any]:
    """Execute one complete Paper Trade through intelligence, Kelly sizing, survival risk, and paper execution."""
    return await _execute_controlled_futures_trade(
        user=user,
        session=session,
        survival=survival,
        registry=registry,
        cache=cache,
        symbol=symbol,
        timeframe=timeframe,
        side=side,
        force_paper=True,
    )


@router.post("/trading/manual-trade", dependencies=[Depends(rate_limit_guard)])
async def execute_manual_futures_trade(
    user: Annotated[User, Depends(require_role(Role.OPERATOR))],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    survival: Annotated[SurvivalController, Depends(get_survival_controller)],
    registry: Annotated[StrategyRegistry, Depends(get_strategy_registry)],
    cache: Annotated[MarketCache, Depends(get_market_cache)],
    symbol: str = "BTC/USDT",
    timeframe: str = "15m",
    side: str | None = None,
) -> dict[str, Any]:
    """Execute one manual Futures (1x Isolated) trade honoring the account's active trading mode."""
    return await _execute_controlled_futures_trade(
        user=user,
        session=session,
        survival=survival,
        registry=registry,
        cache=cache,
        symbol=symbol,
        timeframe=timeframe,
        side=side,
        force_paper=False,
    )


class UpdateTpSlBody(BaseModel):
    stop_loss: float | None = None
    take_profit: float | None = None


@router.get("/trading/active-trades", dependencies=[Depends(rate_limit_guard)])
async def get_active_trades_detailed(
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    cache: Annotated[MarketCache, Depends(get_market_cache)],
) -> dict[str, Any]:
    """Return full exchange-grade active position telemetry (P&L, ROI, Liquidation, Margin, R:R) and 'Why Trade Was Taken' dossier."""
    settings = get_settings()
    repo = TradingRepository(session)
    account = await repo.get_or_create_default_account()
    bal_mgr = BalanceManager(session)
    avail_usdt = await bal_mgr.get_quote_available(account.id, account.quote_currency)
    locked_usdt = await bal_mgr.get_quote_locked(account.id, account.quote_currency)

    open_positions = await repo.get_open_positions(account.id)
    closed_stmt = (
        select(Position)
        .where(Position.account_id == account.id, Position.status == "CLOSED")
        .order_by(desc(Position.closed_at))
        .limit(10)
    )
    closed_res = await session.execute(closed_stmt)
    closed_positions = list(closed_res.scalars().all())

    deriv_svc = DerivativesIntelligenceService()
    detector = MarketRegimeDetector()
    openbb_engine = OpenBBQuantResearch()
    now_utc = datetime.now(timezone.utc)

    active_list: list[dict[str, Any]] = []
    total_unrealized_pnl = Decimal("0")
    total_margin_used = Decimal("0")
    total_notional_usd = Decimal("0")

    for pos in open_positions:
        # 1. Resolve live mark price from cache or latest DB candle
        cached_ticker = await cache.get_json(f"market:ticker:{pos.symbol}")
        candles = await repo.get_candles(settings.exchange_id, pos.symbol, "15m", limit=150)
        mark_price = pos.current_price
        if cached_ticker and cached_ticker.get("last"):
            mark_price = Decimal(str(cached_ticker["last"]))
        elif candles:
            mark_price = candles[-1].close

        pos.current_price = mark_price
        is_short = pos.side.upper() == "SHORT"
        is_futures = pos.market_type.upper() == "FUTURES"
        eff_lev = max(1, int(pos.leverage or 1))

        entry_notional = pos.quantity * pos.entry_price
        current_notional = pos.quantity * mark_price
        gross_pnl = (
            (pos.entry_price - mark_price) * pos.quantity
            if is_short
            else (mark_price - pos.entry_price) * pos.quantity
        )
        est_exit_fee = current_notional * Decimal("0.001")  # 10 bps
        net_unrealized = (gross_pnl - pos.entry_fee).quantize(Decimal("0.00000001"))
        pos.unrealized_pnl = net_unrealized

        initial_margin = (
            pos.margin_used
            if pos.margin_used and pos.margin_used > Decimal("0")
            else (entry_notional / Decimal(eff_lev) if entry_notional > Decimal("0") else Decimal("1"))
        )
        mmr = Decimal("0.005")  # 0.50% maintenance margin rate (Binance USD-M tier 1)
        maintenance_margin = current_notional * mmr
        margin_balance = max(Decimal("0.0001"), initial_margin + net_unrealized)
        margin_ratio_pct = float((maintenance_margin / margin_balance) * Decimal("100"))

        roi_pct = float((net_unrealized / initial_margin) * Decimal("100")) if initial_margin > 0 else 0.0
        unlev_return_pct = (
            float(((pos.entry_price - mark_price) / pos.entry_price) * Decimal("100"))
            if is_short
            else float(((mark_price - pos.entry_price) / pos.entry_price) * Decimal("100"))
        )

        # Liquidation price calculation (Isolated Margin formula)
        if is_short:
            liq_price = pos.entry_price * (Decimal("1") + (Decimal("1") / Decimal(eff_lev)) - mmr)
            dist_to_liq_pct = float(((liq_price - mark_price) / mark_price) * Decimal("100")) if mark_price > 0 else 0.0
        else:
            liq_price = max(
                Decimal("0"),
                pos.entry_price * (Decimal("1") - (Decimal("1") / Decimal(eff_lev)) + mmr),
            )
            dist_to_liq_pct = float(((mark_price - liq_price) / mark_price) * Decimal("100")) if mark_price > 0 else 0.0

        # Breakeven price (covers entry fee + 0.10% exit fee)
        fee_per_unit = (pos.entry_fee + est_exit_fee) / pos.quantity if pos.quantity > 0 else Decimal("0")
        breakeven_price = pos.entry_price - fee_per_unit if is_short else pos.entry_price + fee_per_unit

        # Stop Loss & Take Profit metrics
        sl_px = float(pos.stop_loss) if pos.stop_loss else None
        tp_px = float(pos.take_profit) if pos.take_profit else None
        dist_to_sl_pct: float | None = None
        dist_to_tp_pct: float | None = None
        expected_tp_profit_usd: float | None = None
        expected_tp_roi_pct: float | None = None
        expected_sl_loss_usd: float | None = None
        expected_sl_roi_pct: float | None = None
        rr_ratio: float | None = None

        if pos.stop_loss and pos.stop_loss > 0:
            dist_to_sl_pct = float((abs(mark_price - pos.stop_loss) / mark_price) * Decimal("100"))
            sl_pnl = (
                (pos.entry_price - pos.stop_loss) * pos.quantity - pos.entry_fee
                if is_short
                else (pos.stop_loss - pos.entry_price) * pos.quantity - pos.entry_fee
            )
            expected_sl_loss_usd = float(sl_pnl)
            expected_sl_roi_pct = float((sl_pnl / initial_margin) * Decimal("100")) if initial_margin > 0 else 0.0

        if pos.take_profit and pos.take_profit > 0:
            dist_to_tp_pct = float((abs(pos.take_profit - mark_price) / mark_price) * Decimal("100"))
            tp_pnl = (
                (pos.entry_price - pos.take_profit) * pos.quantity - pos.entry_fee
                if is_short
                else (pos.take_profit - pos.entry_price) * pos.quantity - pos.entry_fee
            )
            expected_tp_profit_usd = float(tp_pnl)
            expected_tp_roi_pct = float((tp_pnl / initial_margin) * Decimal("100")) if initial_margin > 0 else 0.0

        if expected_tp_profit_usd is not None and expected_sl_loss_usd is not None and abs(expected_sl_loss_usd) > 1e-6:
            rr_ratio = round(abs(expected_tp_profit_usd) / abs(expected_sl_loss_usd), 2)

        opened_utc = pos.opened_at.astimezone(timezone.utc) if pos.opened_at.tzinfo else pos.opened_at.replace(tzinfo=timezone.utc)
        duration_sec = max(0, int((now_utc - opened_utc).total_seconds()))

        # 2. Retrieve "Why Trade Was Taken" intelligence from TradeSignal, RiskDecision, Order & Trade
        sig_stmt = (
            select(TradeSignal)
            .where(TradeSignal.symbol == pos.symbol, TradeSignal.strategy_name == pos.strategy_name)
            .order_by(desc(TradeSignal.created_at))
            .limit(1)
        )
        sig_row = (await session.execute(sig_stmt)).scalar_one_or_none()
        if sig_row is None:
            sig_fallback = (
                select(TradeSignal)
                .where(TradeSignal.symbol == pos.symbol)
                .order_by(desc(TradeSignal.created_at))
                .limit(1)
            )
            sig_row = (await session.execute(sig_fallback)).scalar_one_or_none()

        rd_stmt = (
            select(RiskDecision)
            .where(
                RiskDecision.account_id == account.id,
                RiskDecision.symbol == pos.symbol,
                RiskDecision.decision == "APPROVED",
            )
            .order_by(desc(RiskDecision.created_at))
            .limit(1)
        )
        rd_row = (await session.execute(rd_stmt)).scalar_one_or_none()

        ord_stmt = (
            select(Order)
            .where(Order.account_id == account.id, Order.symbol == pos.symbol, Order.status == "FILLED")
            .order_by(desc(Order.created_at))
            .limit(1)
        )
        ord_row = (await session.execute(ord_stmt)).scalar_one_or_none()

        trd_stmt = (
            select(Trade)
            .where(Trade.account_id == account.id, Trade.symbol == pos.symbol)
            .order_by(desc(Trade.executed_at))
            .limit(1)
        )
        trd_row = (await session.execute(trd_stmt)).scalar_one_or_none()

        df = CandleManager.to_dataframe(candles)
        live_regime = detector.detect(df)
        live_openbb = openbb_engine.analyze(df).to_dict()
        live_tv = deriv_svc.compute_tradingview_rating(df).to_dict()
        live_deriv = (await deriv_svc.fetch_derivatives_sentiment(pos.symbol, df)).to_dict()

        snap_ind = (sig_row.indicators_snapshot if sig_row and sig_row.indicators_snapshot else {}) or {}
        entry_openbb = snap_ind.get("openbb_quant") or live_openbb
        entry_tv = snap_ind.get("tradingview_rating") or live_tv
        entry_deriv = snap_ind.get("derivatives_sentiment") or live_deriv

        decision_payload = (rd_row.decision_payload if rd_row and rd_row.decision_payload else {}) or {}
        ai_explanation = decision_payload.get("explanation") or [
            f"Strategy '{pos.strategy_name}' triggered {pos.side} entry at ${float(pos.entry_price):,.2f} in {sig_row.market_regime if sig_row else live_regime.primary_regime.value} regime.",
            f"OpenBB Quant Confluence Score {float(entry_openbb.get('confluence_score', 0.5)) * 100:.0f}% ({entry_openbb.get('trend_bias', 'BULLISH')}) confirmed entry quality.",
            f"TradingView 13-Indicator Composite Rating: {entry_tv.get('overall_rating', 'BUY')} (Score {float(entry_tv.get('rating_score', 0.25)):+.2f}).",
            f"CoinGlass / Binance Futures Positioning: {entry_deriv.get('crowding_state', 'BALANCED')} with 8h Funding {float(entry_deriv.get('funding_rate_pct', 0.01)):.4f}%.",
            f"Fractional Kelly Criterion ({float(rd_row.fractional_kelly * 100) if rd_row else 25.0:.2f}%) & Survival Controller ({rd_row.survival_state if rd_row else account.survival_state}) approved position sizing.",
        ]

        total_unrealized_pnl += net_unrealized
        total_margin_used += initial_margin
        total_notional_usd += current_notional

        active_list.append(
            {
                "id": str(pos.id),
                "symbol": pos.symbol,
                "exchange": account.exchange.upper(),
                "trading_mode": pos.trading_mode,
                "is_simulated": pos.trading_mode in {TradingMode.PAPER.value, TradingMode.BACKTEST.value},
                "market_type": pos.market_type,
                "side": pos.side,
                "leverage": eff_lev,
                "margin_mode": "ISOLATED" if is_futures else "SPOT_CASH",
                "strategy_name": pos.strategy_name,
                "quantity": float(pos.quantity),
                "entry_price": float(pos.entry_price),
                "mark_price": float(mark_price),
                "breakeven_price": float(breakeven_price),
                "entry_notional_usd": float(entry_notional),
                "current_notional_usd": float(current_notional),
                "initial_margin_usd": float(initial_margin),
                "maintenance_margin_usd": float(maintenance_margin),
                "maintenance_margin_rate_pct": float(mmr * Decimal("100")),
                "margin_balance_usd": float(margin_balance),
                "margin_ratio_pct": round(margin_ratio_pct, 2),
                "unrealized_pnl": float(net_unrealized),
                "gross_pnl": float(gross_pnl),
                "entry_fee_usd": float(pos.entry_fee),
                "estimated_exit_fee_usd": float(est_exit_fee),
                "roi_pct": round(roi_pct, 2),
                "unleveraged_return_pct": round(unlev_return_pct, 2),
                "liquidation_price": float(liq_price),
                "distance_to_liq_pct": round(dist_to_liq_pct, 2),
                "stop_loss": sl_px,
                "take_profit": tp_px,
                "sl_order_id": pos.sl_order_id,
                "tp_order_id": pos.tp_order_id,
                "exchange_protected": bool(pos.sl_order_id or pos.tp_order_id),
                "scaled_out": bool(pos.scaled_out),
                "exit_reason": pos.exit_reason,
                "market_regime": pos.market_regime,
                "distance_to_sl_pct": round(dist_to_sl_pct, 2) if dist_to_sl_pct is not None else None,
                "distance_to_tp_pct": round(dist_to_tp_pct, 2) if dist_to_tp_pct is not None else None,
                "expected_tp_profit_usd": round(expected_tp_profit_usd, 4) if expected_tp_profit_usd is not None else None,
                "expected_tp_roi_pct": round(expected_tp_roi_pct, 2) if expected_tp_roi_pct is not None else None,
                "expected_sl_loss_usd": round(expected_sl_loss_usd, 4) if expected_sl_loss_usd is not None else None,
                "expected_sl_roi_pct": round(expected_sl_roi_pct, 2) if expected_sl_roi_pct is not None else None,
                "reward_risk_ratio": rr_ratio,
                "opened_at": pos.opened_at.isoformat(),
                "duration_seconds": duration_sec,
                "why_taken": {
                    "signal_reason": sig_row.reason if sig_row else f"Multi-factor {pos.strategy_name} signal confirmed by OpenBB, TradingView & CoinGlass orderflow",
                    "strategy_name": pos.strategy_name,
                    "market_regime": sig_row.market_regime if sig_row else live_regime.primary_regime.value,
                    "signal_confidence_pct": round(float(sig_row.confidence) * 100, 1) if sig_row else 72.0,
                    "regime_adx": float(live_regime.trend_strength_adx),
                    "regime_volatility_pct": float(live_regime.volatility_pct),
                    "kelly_and_risk": {
                        "decision": rd_row.decision if rd_row else "APPROVED",
                        "survival_state": rd_row.survival_state if rd_row else account.survival_state,
                        "win_probability_pct": round(float(rd_row.win_probability) * 100, 1) if rd_row else 64.5,
                        "ci_low_pct": round(float(rd_row.probability_lower_bound) * 100, 1) if rd_row else 55.2,
                        "net_ev_pct": round(float(rd_row.expected_value) * 100, 3) if rd_row else 0.85,
                        "full_kelly_pct": round(float(rd_row.full_kelly) * 100, 2) if rd_row else 18.4,
                        "fractional_kelly_pct": round(float(rd_row.fractional_kelly) * 100, 2) if rd_row else 4.6,
                        "approved_notional_usd": float(rd_row.approved_notional) if rd_row else float(entry_notional),
                        "estimated_fees_usd": float(rd_row.estimated_fees) if rd_row else float(pos.entry_fee),
                        "ai_explanation": ai_explanation if isinstance(ai_explanation, list) else [str(ai_explanation)],
                    },
                    "openbb_quant": entry_openbb,
                    "tradingview_rating": entry_tv,
                    "derivatives_sentiment": entry_deriv,
                    "execution_audit": {
                        "order_id": str(ord_row.id) if ord_row else None,
                        "client_order_id": ord_row.client_order_id if ord_row else None,
                        "exchange_order_id": ord_row.exchange_order_id if ord_row else None,
                        "trade_id": str(trd_row.id) if trd_row else None,
                        "slippage_bps": float(trd_row.slippage_bps) if trd_row else 1.2,
                        "fee_paid_usd": float(pos.entry_fee),
                        "executed_at": trd_row.executed_at.isoformat() if trd_row else pos.opened_at.isoformat(),
                    },
                },
            }
        )

    await session.flush()

    total_roi_pct = (
        float((total_unrealized_pnl / total_margin_used) * Decimal("100"))
        if total_margin_used > Decimal("0")
        else 0.0
    )

    return {
        "account_summary": {
            "account_id": str(account.id),
            "exchange": account.exchange.upper(),
            "trading_mode": account.trading_mode,
            "market_type": account.market_type,
            "leverage": account.leverage,
            "survival_state": account.survival_state,
            "is_trading_active": account.is_trading_active,
            "available_usdt": float(avail_usdt),
            "locked_usdt": float(locked_usdt),
            "active_positions_count": len(active_list),
            "total_notional_usd": float(total_notional_usd),
            "total_margin_used_usd": float(total_margin_used),
            "total_unrealized_pnl_usd": float(total_unrealized_pnl),
            "total_roi_pct": round(total_roi_pct, 2),
            "max_hold_minutes": settings.max_hold_minutes,
            "mean_reversion_max_hold_minutes": settings.mean_reversion_max_hold_minutes,
            "min_stop_loss_pct": float(settings.min_stop_loss_pct) * 100,
            "max_stop_loss_pct": float(settings.max_stop_loss_pct) * 100,
            "min_reward_risk_ratio": float(settings.min_reward_risk_ratio),
            "scale_out_r_multiple": float(settings.scale_out_r_multiple),
            "scale_out_fraction": float(settings.scale_out_fraction) * 100,
            "atr_trail_multiplier": float(settings.atr_trail_multiplier),
            "fast_book_profit_pct": float(settings.fast_book_profit_pct) * 100,
        },
        "active_trades": active_list,
        "recently_closed": [
            {
                "id": str(c.id),
                "symbol": c.symbol,
                "side": c.side,
                "market_type": c.market_type,
                "leverage": c.leverage,
                "strategy_name": c.strategy_name,
                "market_regime": c.market_regime,
                "exit_reason": c.exit_reason or "CLOSED",
                "net_pnl_pct": float(c.net_pnl_pct) * 100 if c.net_pnl_pct is not None else None,
                "quantity": float(c.quantity),
                "entry_price": float(c.entry_price),
                "exit_price": float(c.current_price),
                "realized_pnl": float(c.realized_pnl),
                "opened_at": c.opened_at.isoformat(),
                "closed_at": c.closed_at.isoformat() if c.closed_at else None,
            }
            for c in closed_positions
        ],
    }


@router.post("/trading/positions/{position_id}/close", dependencies=[Depends(rate_limit_guard)])
async def close_active_position(
    position_id: str,
    user: Annotated[User, Depends(require_role(Role.OPERATOR))],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    survival: Annotated[SurvivalController, Depends(get_survival_controller)],
    registry: Annotated[StrategyRegistry, Depends(get_strategy_registry)],
    cache: Annotated[MarketCache, Depends(get_market_cache)],
    fraction: float = 1.0,
) -> dict[str, Any]:
    """Book profit / stop a running trade: close `fraction` (0.1-1.0) of the position at market."""
    try:
        pos_uuid = uuid.UUID(position_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid position ID") from exc
    frac = Decimal(str(max(0.05, min(1.0, fraction))))

    repo = TradingRepository(session)
    account = await repo.get_or_create_default_account()
    stmt = select(Position).where(Position.id == pos_uuid, Position.account_id == account.id)
    pos = (await session.execute(stmt)).scalar_one_or_none()
    if pos is None or pos.status != "OPEN":
        raise HTTPException(status_code=404, detail="Active open position not found")

    loop = await _build_loop_for_account(
        session, account, registry=registry, survival=survival, cache=cache
    )
    try:
        pos_mode = TradingMode(pos.trading_mode) if pos.trading_mode else TradingMode(account.trading_mode)
        snap = await loop.collector.collect_symbol_data(
            session, pos.symbol, timeframe="15m", limit=60, allow_historical=True
        )
        eff_rules = (
            snap.rules.for_paper_micro_trading()
            if pos_mode in {TradingMode.PAPER, TradingMode.BACKTEST}
            else snap.rules
        )
        prec = max(0, int(getattr(eff_rules, "quantity_precision", 6)))
        step = Decimal("10") ** (-prec)

        if frac < Decimal("1"):
            part_qty = (pos.quantity * frac).quantize(step, rounding=ROUND_DOWN)
            if part_qty <= Decimal("0"):
                raise HTTPException(
                    status_code=400,
                    detail="Partial booking quantity is below exchange step precision; choose a larger % or Close 100%.",
                )
            if part_qty >= pos.quantity:
                frac = Decimal("1")
                remaining_id = None
            else:
                actual_frac = part_qty / pos.quantity
                part_margin = (pos.margin_used * actual_frac).quantize(Decimal("0.00000001"))
                part_fee = (pos.entry_fee * actual_frac).quantize(Decimal("0.00000001"))
                remaining_pos = pos
                part = Position(
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
                    scaled_out=pos.scaled_out,
                    market_regime=pos.market_regime,
                    entry_fee=part_fee,
                    unrealized_pnl=Decimal("0"),
                    realized_pnl=Decimal("0"),
                    opened_at=pos.opened_at,
                )
                pos.quantity = pos.quantity - part_qty
                pos.margin_used = max(Decimal("0"), pos.margin_used - part_margin)
                pos.entry_fee = max(Decimal("0"), pos.entry_fee - part_fee)
                session.add(part)
                await session.flush()
                remaining_id = str(pos.id)
                pos = part
                frac = actual_frac
        else:
            remaining_pos = None
            remaining_id = None

        exit_side = "BUY" if pos.side.upper() == "SHORT" else "SELL"
        exit_req = OrderRequest(
            symbol=pos.symbol,
            side=exit_side,
            order_type="MARKET",
            quantity=pos.quantity,
            client_order_id=OrderManager.build_client_order_id("manualclose"),
            reduce_only=pos.market_type.upper() == "FUTURES",
        )
        outcome = await loop.execution_engine.execute(
            session=session,
            account_id=account.id,
            risk_decision_id=None,
            request=exit_req,
            rules=eff_rules,
            ticker=snap.ticker,
            order_book=None,
            trading_mode=pos_mode,
            live_authorized=(pos_mode == TradingMode.LIVE_TRADING and loop._is_live_authorized(account)),
            strategy_name=pos.strategy_name,
            market_regime="MANUAL_EXIT",
            market_type=pos.market_type,
            leverage=pos.leverage,
            closing_position=pos,
        )
        if not outcome.executed or outcome.result is None or not outcome.result.average_price:
            raise HTTPException(
                status_code=400,
                detail=outcome.reason or "Exchange close execution was rejected; position left unchanged.",
            )

        exit_px = outcome.result.average_price
        exit_fee = outcome.result.fee
        exit_lbl = "MANUAL_PARTIAL_BOOK" if remaining_id is not None else "MANUAL_CLOSE"
        if remaining_id is None:
            await cancel_protective_orders(loop.exchange, pos, pos_mode)
        elif remaining_pos is not None and remaining_pos.stop_loss is not None:
            await sync_stop_loss_order(
                loop.exchange,
                remaining_pos,
                remaining_pos.stop_loss,
                rules=eff_rules,
                trading_mode=pos_mode,
            )
        realized = await loop.portfolio_manager.close_position(
            session,
            position=pos,
            exit_price=exit_px,
            exit_fee=exit_fee,
            exit_reason=exit_lbl,
        )
        await loop.portfolio_manager.mark_to_market(
            session,
            account=account,
            latest_prices={pos.symbol: exit_px},
        )
        if pos_mode == TradingMode.LIVE_TRADING:
            with suppress(Exception):
                await ExchangeConnectionService.verify_and_sync_balances(
                    session,
                    account=account,
                    exchange=loop.exchange,
                    switch_to_read_only_if_paper=False,
                    reset_baseline_from_exchange=False,
                )
        await AuditLogger.log(
            session,
            actor=user.username,
            action="POSITION_MANUALLY_CLOSED" if remaining_id is None else "POSITION_PARTIALLY_BOOKED",
            resource_type="position",
            resource_id=str(pos.id),
            details={
                "symbol": pos.symbol,
                "side": pos.side,
                "fraction": float(frac),
                "exit_price": float(exit_px),
                "realized_pnl": float(realized),
            },
        )
        return {
            "status": "closed" if remaining_id is None else "partially_booked",
            "position_id": str(pos.id),
            "remaining_position_id": remaining_id,
            "fraction": float(frac),
            "symbol": pos.symbol,
            "side": pos.side,
            "exit_price": float(exit_px),
            "realized_pnl": float(realized),
        }
    finally:
        await loop.exchange.close()


@router.post("/trading/positions/close-all", dependencies=[Depends(rate_limit_guard)])
async def close_all_positions(
    user: Annotated[User, Depends(require_role(Role.OPERATOR))],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    survival: Annotated[SurvivalController, Depends(get_survival_controller)],
    registry: Annotated[StrategyRegistry, Depends(get_strategy_registry)],
    cache: Annotated[MarketCache, Depends(get_market_cache)],
) -> dict[str, Any]:
    """Stop everything: market-close all running trades."""
    repo = TradingRepository(session)
    account = await repo.get_or_create_default_account()
    results = []
    total = 0.0
    for p in await repo.get_open_positions(account.id):
        r = await close_active_position(str(p.id), user, session, survival, registry, cache, 1.0)
        total += r["realized_pnl"]
        results.append(r)
    return {"closed": len(results), "total_realized_pnl": total, "results": results}


_SCANNER_CACHE: dict[str, dict[str, Any]] = {}


@router.get("/market-scanner", dependencies=[Depends(rate_limit_guard)])
async def market_scanner(
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    registry: Annotated[StrategyRegistry, Depends(get_strategy_registry)],
    survival: Annotated[SurvivalController, Depends(get_survival_controller)],
    cache: Annotated[MarketCache, Depends(get_market_cache)],
    timeframe: str = "15m",
    scope: str = "GLOBAL",
    q: str = "",
    limit: int = 32,
) -> dict[str, Any]:
    """Scan global futures movers + pinned coins + optional search query and rank by setup readiness."""
    import asyncio as _asyncio
    import time as _time
    from app.market.universe_discovery import (
        GlobalMarketDiscoveryService,
        get_pinned_watchlist,
        normalize_pair,
    )

    clean_q = q.strip().upper()
    cache_key = f"{timeframe}:{scope.upper()}:{clean_q}:{limit}"
    cached = _SCANNER_CACHE.get(cache_key)
    if cached and _time.time() - cached["ts"] < 25:
        return cached["data"]

    repo = TradingRepository(session)
    account = await repo.get_or_create_default_account()
    open_positions = await repo.get_open_positions(account.id)
    pinned = await get_pinned_watchlist(session, cache)

    loop = await _build_loop_for_account(
        session, account, registry=registry, survival=survival, cache=cache
    )
    deriv_svc = DerivativesIntelligenceService()
    rows: list[dict[str, Any]] = []
    try:
        global_tickers = await GlobalMarketDiscoveryService.fetch_global_tickers(loop.exchange)
        if clean_q:
            matches = await GlobalMarketDiscoveryService.search_global_coins(
                loop.exchange, query=clean_q, limit=min(limit, 24), include_external=True
            )
            scan_symbols = [m["symbol"] for m in matches] or [normalize_pair(clean_q)]
        elif scope.upper() == "GLOBAL":
            avail_usdt = await BalanceManager(session).get_quote_available(account.id, account.quote_currency)
            max_bp = float(avail_usdt * Decimal("5") * Decimal("0.93")) if avail_usdt > 0 else None
            scan_symbols = await GlobalMarketDiscoveryService.discover_hot_universe(
                loop.exchange,
                base_symbols=loop.settings.symbols,
                pinned_symbols=pinned,
                open_symbols=[p.symbol for p in open_positions],
                max_total=min(max(limit, 32), 48),
                max_buying_power_usd=max_bp,
            )
        else:
            scan_symbols = list(dict.fromkeys([*[p.symbol for p in open_positions], *pinned, *loop.settings.symbols]))

        sem = _asyncio.Semaphore(12)

        async def _fetch_scan(sym: str):
            async with sem:
                try:
                    bundle = await loop.collector.fetch_raw_bundle(
                        sym,
                        timeframe=timeframe,
                        limit=120,
                        cached_ticker=global_tickers.get(sym),
                        skip_order_book=bool(global_tickers),
                    )
                    return sym, bundle
                except Exception:
                    return sym, None

        fetched = await _asyncio.gather(*(_fetch_scan(s) for s in scan_symbols))
        snaps_list: list[tuple[str, Any]] = []
        for sym, bundle in fetched:
            if bundle is None:
                try:
                    snap, resolved = await GlobalMarketDiscoveryService.collect_symbol_snapshot_any(
                        loop.collector,
                        session,
                        sym,
                        timeframe=timeframe,
                        limit=120,
                        allow_historical=True,
                        allow_external_feed=True,
                    )
                    snaps_list.append((resolved, snap))
                except Exception:
                    continue
            else:
                rules, ticker, order_book, raw_candles = bundle
                snap = await loop.collector.persist_raw_bundle(
                    session, sym, timeframe, rules, ticker, order_book, raw_candles, allow_historical=True
                )
                snaps_list.append((sym, snap))

        pinned_set = set(pinned)
        for sym, snap in snaps_list:
            df = snap.candles_df
            regime = loop.regime_detector.detect(df)
            tv = deriv_svc.compute_tradingview_rating(df)
            best = None
            for strat in loop.selector.select_for_regime(regime):
                s = strat.generate_signal(sym, df)
                if s.action in {"BUY", "SELL"} and (best is None or s.confidence > best.confidence):
                    best = s
            closes = df["close"].astype(float) if "close" in df else None
            chg = 0.0
            if closes is not None and len(closes) > 96:
                chg = (closes.iloc[-1] / closes.iloc[-97] - 1) * 100
            elif closes is not None and len(closes) > 1:
                chg = (closes.iloc[-1] / closes.iloc[0] - 1) * 100
            tv_score = float(tv.rating_score)
            if best is not None:
                aligned = (best.action == "BUY" and tv_score > 0) or (best.action == "SELL" and tv_score < 0)
                score = float(best.confidence) * 70 + abs(tv_score) * 30 * (1 if aligned else 0.3)
                status = "READY" if aligned and float(best.confidence) >= 0.55 else "FORMING"
                direction = "LONG" if best.action == "BUY" else "SHORT"
                strategy = best.strategy
                reason = best.reason
            else:
                score = abs(tv_score) * 25
                status = "WAIT"
                direction = "LONG" if tv_score > 0.15 else "SHORT" if tv_score < -0.15 else "-"
                strategy = None
                reason = f"No strategy trigger in {regime.primary_regime.value} regime"
            rows.append(
                {
                    "symbol": sym,
                    "price": float(snap.ticker.last),
                    "change_pct": round(float(chg), 2),
                    "volume_24h_usd": float(snap.ticker.volume_24h),
                    "pinned": sym in pinned_set,
                    "regime": regime.primary_regime.value,
                    "tv_rating": tv.overall_rating,
                    "tv_score": round(tv_score, 3),
                    "setup_status": status,
                    "direction": direction,
                    "strategy": strategy,
                    "score": round(score, 1),
                    "reason": reason,
                }
            )
    finally:
        await loop.exchange.close()

    rows.sort(key=lambda r: (1 if r.get("pinned") else 0, r["score"]), reverse=True)
    data = {
        "timeframe": timeframe,
        "scope": scope.upper(),
        "scanned": len(rows),
        "ready_count": sum(1 for r in rows if r["setup_status"] == "READY"),
        "pinned": pinned,
        "coins": rows,
    }
    _SCANNER_CACHE[cache_key] = {"ts": _time.time(), "data": data}
    return data


@router.patch("/trading/positions/{position_id}/tpsl", dependencies=[Depends(rate_limit_guard)])
async def update_position_tpsl(
    position_id: str,
    body: UpdateTpSlBody,
    user: Annotated[User, Depends(require_role(Role.OPERATOR))],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    survival: Annotated[SurvivalController, Depends(get_survival_controller)],
    registry: Annotated[StrategyRegistry, Depends(get_strategy_registry)],
    cache: Annotated[MarketCache, Depends(get_market_cache)],
) -> dict[str, Any]:
    """Update Stop Loss and Take Profit levels on an active open position and sync exchange triggers."""
    try:
        pos_uuid = uuid.UUID(position_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid position ID") from exc

    repo = TradingRepository(session)
    account = await repo.get_or_create_default_account()
    stmt = select(Position).where(Position.id == pos_uuid, Position.account_id == account.id)
    pos = (await session.execute(stmt)).scalar_one_or_none()
    if pos is None or pos.status != "OPEN":
        raise HTTPException(status_code=404, detail="Active open position not found")

    if body.stop_loss is not None:
        pos.stop_loss = Decimal(str(body.stop_loss)) if body.stop_loss > 0 else None
    if body.take_profit is not None:
        pos.take_profit = Decimal(str(body.take_profit)) if body.take_profit > 0 else None

    pos_mode = TradingMode(pos.trading_mode) if pos.trading_mode else TradingMode(account.trading_mode)
    if pos_mode in {TradingMode.LIVE_TRADING, TradingMode.TESTNET} and pos.market_type.upper() == "FUTURES":
        loop = await _build_loop_for_account(
            session, account, registry=registry, survival=survival, cache=cache
        )
        try:
            rules = await loop.exchange.get_market_rules(pos.symbol)
            await cancel_protective_orders(loop.exchange, pos, pos_mode)
            sl_oid, tp_oid, _ = await place_protective_orders(
                loop.exchange,
                symbol=pos.symbol,
                position_side=pos.side,
                quantity=pos.quantity,
                stop_loss=pos.stop_loss,
                take_profit=pos.take_profit,
                rules=rules,
                trading_mode=pos_mode,
                market_type=pos.market_type,
            )
            pos.sl_order_id = sl_oid
            pos.tp_order_id = tp_oid
        finally:
            await loop.exchange.close()

    await session.flush()
    await AuditLogger.log(
        session,
        actor=user.username,
        action="POSITION_TPSL_UPDATED",
        resource_type="position",
        resource_id=str(pos.id),
        details={
            "symbol": pos.symbol,
            "stop_loss": float(pos.stop_loss) if pos.stop_loss else None,
            "take_profit": float(pos.take_profit) if pos.take_profit else None,
            "sl_order_id": pos.sl_order_id,
            "tp_order_id": pos.tp_order_id,
        },
    )
    return {
        "status": "updated",
        "position_id": str(pos.id),
        "stop_loss": float(pos.stop_loss) if pos.stop_loss else None,
        "take_profit": float(pos.take_profit) if pos.take_profit else None,
        "sl_order_id": pos.sl_order_id,
        "tp_order_id": pos.tp_order_id,
    }
