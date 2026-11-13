"use client";

import React from "react";
import type {
  DashboardData,
  ExchangeStatusData,
  PortfolioData,
  TradesData,
} from "@/types/trading";

interface AccountTopBarProps {
  dashboard: DashboardData;
  portfolio: PortfolioData | null;
  exchangeStatus: ExchangeStatusData | null;
  trades: TradesData | null;
}

export default function AccountTopBar({
  dashboard,
  portfolio,
  exchangeStatus,
  trades,
}: AccountTopBarProps) {
  const { account, overview, survival } = dashboard;
  const balances = (portfolio?.balances || []).filter(
    (b) => b.total >= 0.0001 || b.asset === "USDT"
  );
  const totalTrades = trades?.trades?.length || 0;
  const winningTrades =
    trades?.trades?.filter((t) => (t.realized_pnl ?? 0) > 0).length || 0;
  const recentWinRate =
    totalTrades > 0 ? ((winningTrades / totalTrades) * 100).toFixed(0) : "0";

  const isLiveTrading = account.trading_mode === "LIVE_TRADING";
  const leverage = account.leverage || 1;

  return (
    <section className="rounded-xl border border-indigo-500/35 bg-gradient-to-r from-slate-900/95 via-slate-900/90 to-indigo-950/50 p-4 shadow-xl">
      {/* Row 1: Account Identity, Mode, Exchange, Market Type & Wallet Asset Pills */}
      <div className="flex flex-col gap-3 border-b border-slate-800/80 pb-3 lg:flex-row lg:items-center lg:justify-between">
        <div className="flex flex-wrap items-center gap-2.5">
          <div className="flex items-center gap-2 rounded-lg border border-slate-700 bg-slate-950/90 px-3 py-1.5">
            <span className="h-2.5 w-2.5 rounded-full bg-emerald-400 animate-pulse" />
            <span className="text-xs font-semibold uppercase tracking-wider text-slate-400">
              Account:
            </span>
            <span className="text-sm font-bold text-white">
              {account.name || "Primary Survival Account"}
            </span>
          </div>

          <span
            className={`rounded-lg border px-2.5 py-1 text-xs font-bold tracking-wide ${
              isLiveTrading
                ? "border-emerald-500/60 bg-emerald-500/20 text-emerald-300"
                : "border-cyan-500/60 bg-cyan-500/20 text-cyan-300"
            }`}
          >
            {isLiveTrading
              ? "LIVE REAL-USDT TRADING"
              : "LIVE READ-ONLY (REAL SYNC)"}
          </span>

          <span className="rounded-lg border border-amber-500/50 bg-amber-500/15 px-2.5 py-1 text-xs font-bold text-amber-300">
            USD-M FUTURES • {leverage || 1}x ISOLATED
          </span>

          <span className="rounded-lg border border-slate-700 bg-slate-950/80 px-2.5 py-1 text-xs text-slate-300">
            Exchange:{" "}
            <strong className="uppercase text-white">
              {exchangeStatus?.exchange_id || account.exchange}
            </strong>{" "}
            {exchangeStatus?.verified ? (
              <span className="ml-1 text-emerald-400">
                ● API Verified ({exchangeStatus.api_key_mask})
              </span>
            ) : (
              <span className="ml-1 text-amber-400">● Connect Exchange Keys</span>
            )}
          </span>
        </div>

        {/* Wallet Asset Breakdown Pills */}
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-[11px] font-semibold uppercase tracking-wider text-slate-400">
            Wallet Balances:
          </span>
          {balances.length > 0 ? (
            balances.map((b) => (
              <div
                key={b.asset}
                className="flex items-center gap-1.5 rounded-lg border border-slate-700/80 bg-slate-950/90 px-2.5 py-1 text-xs"
              >
                <span className="font-bold text-indigo-300">{b.asset}</span>
                <span className="font-mono font-semibold text-white">
                  {b.asset === "USDT"
                    ? `$${b.total.toFixed(4)}`
                    : b.total.toFixed(6)}
                </span>
                {b.locked > 0 && (
                  <span className="text-[10px] text-amber-400">
                    (Locked: {b.locked.toFixed(2)})
                  </span>
                )}
              </div>
            ))
          ) : (
            <div className="rounded-lg border border-slate-800 bg-slate-950/80 px-2.5 py-1 text-xs font-mono text-slate-300">
              USDT: ${overview.available_balance.toFixed(4)}
            </div>
          )}
        </div>
      </div>

      {/* Row 2: High-Visibility Financial Telemetry Strip */}
      <div className="mt-3 grid grid-cols-2 gap-2.5 sm:grid-cols-4 lg:grid-cols-8">
        <div className="rounded-lg border border-slate-800 bg-slate-950/85 p-2.5">
          <div className="text-[10px] font-semibold uppercase tracking-wider text-slate-400">
            Total Account Equity
          </div>
          <div className="mt-1 font-mono text-lg font-bold text-white">
            ${overview.current_equity.toFixed(4)}
          </div>
          <div className="text-[11px] text-slate-400">
            Baseline: ${overview.initial_capital.toFixed(2)}
          </div>
        </div>

        <div className="rounded-lg border border-slate-800 bg-slate-950/85 p-2.5">
          <div className="text-[10px] font-semibold uppercase tracking-wider text-slate-400">
            Available USDT Balance
          </div>
          <div className="mt-1 font-mono text-lg font-bold text-emerald-400">
            ${overview.available_balance.toFixed(4)}
          </div>
          <div className="text-[11px] text-slate-400">Free Cash</div>
        </div>

        <div className="rounded-lg border border-slate-800 bg-slate-950/85 p-2.5">
          <div className="text-[10px] font-semibold uppercase tracking-wider text-slate-400">
            Locked / Margin Used
          </div>
          <div className="mt-1 font-mono text-lg font-bold text-amber-300">
            ${overview.locked_balance.toFixed(4)}
          </div>
          <div className="text-[11px] text-slate-400">
            Open Pos: {survival.open_positions_count}/
            {survival.max_open_positions}
          </div>
        </div>

        <div className="rounded-lg border border-slate-800 bg-slate-950/85 p-2.5">
          <div className="text-[10px] font-semibold uppercase tracking-wider text-slate-400">
            Net Total Return
          </div>
          <div
            className={`mt-1 font-mono text-lg font-bold ${
              overview.net_return_pct >= 0
                ? "text-emerald-400"
                : "text-rose-400"
            }`}
          >
            {overview.net_return_pct >= 0 ? "+" : ""}
            {(overview.net_return_pct * 100).toFixed(2)}%
          </div>
          <div className="text-[11px] text-slate-400">
            Fees Paid: ${overview.total_trading_costs.toFixed(4)}
          </div>
        </div>

        <div className="rounded-lg border border-slate-800 bg-slate-950/85 p-2.5">
          <div className="text-[10px] font-semibold uppercase tracking-wider text-slate-400">
            Realized Profit / Loss
          </div>
          <div
            className={`mt-1 font-mono text-lg font-bold ${
              overview.realized_pnl >= 0 ? "text-emerald-400" : "text-rose-400"
            }`}
          >
            {overview.realized_pnl >= 0 ? "+" : ""}$
            {overview.realized_pnl.toFixed(4)}
          </div>
          <div className="text-[11px] text-slate-400">
            Trades: {totalTrades} ({recentWinRate}% Win)
          </div>
        </div>

        <div className="rounded-lg border border-slate-800 bg-slate-950/85 p-2.5">
          <div className="text-[10px] font-semibold uppercase tracking-wider text-slate-400">
            Unrealized Open P&L
          </div>
          <div
            className={`mt-1 font-mono text-lg font-bold ${
              overview.unrealized_pnl >= 0
                ? "text-emerald-400"
                : "text-rose-400"
            }`}
          >
            {overview.unrealized_pnl >= 0 ? "+" : ""}$
            {overview.unrealized_pnl.toFixed(4)}
          </div>
          <div className="text-[11px] text-slate-400">
            Pos Value: ${overview.open_position_value.toFixed(2)}
          </div>
        </div>

        <div className="rounded-lg border border-slate-800 bg-slate-950/85 p-2.5">
          <div className="text-[10px] font-semibold uppercase tracking-wider text-slate-400">
            Current Drawdown
          </div>
          <div
            className={`mt-1 font-mono text-lg font-bold ${
              overview.current_drawdown_pct > 0.025
                ? "text-amber-400"
                : "text-slate-100"
            }`}
          >
            -{(overview.current_drawdown_pct * 100).toFixed(2)}%
          </div>
          <div className="text-[11px] text-slate-400">
            Max Allowed: -{(survival.max_drawdown * 100).toFixed(0)}%
          </div>
        </div>

        <div className="rounded-lg border border-slate-800 bg-slate-950/85 p-2.5">
          <div className="text-[10px] font-semibold uppercase tracking-wider text-slate-400">
            Survival Governor
          </div>
          <div className="mt-1 font-mono text-sm font-bold text-indigo-300">
            {survival.state}
          </div>
          <div className="text-[11px] text-slate-400">
            Kelly {(survival.kelly_multiplier * 100).toFixed(0)}% • Max Risk{" "}
            {(survival.max_risk_per_trade * 100).toFixed(2)}%
          </div>
        </div>
      </div>
    </section>
  );
}
