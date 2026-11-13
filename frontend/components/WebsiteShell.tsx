"use client";

import React from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  Activity,
  BarChart3,
  BookOpen,
  CandlestickChart,
  Play,
  Radar,
  RefreshCw,
  ShieldAlert,
  Square,
  Wallet,
  Zap,
} from "lucide-react";
import { SurvivalBadge } from "@/components/SurvivalBadge";
import { fmtUsd, useTradingContext } from "@/context/TradingContext";

const NAV_ITEMS = [
  { href: "/", label: "Exchange", icon: CandlestickChart },
  { href: "/trade", label: "Active Trade", icon: Activity },
  { href: "/dashboard", label: "Analytics & API", icon: BarChart3 },
  { href: "/guide", label: "Agent Guide", icon: BookOpen },
] as const;

export function WebsiteShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const {
    dash,
    active,
    portfolio,
    exchangeStatus,
    busy,
    toast,
    syncBalance,
    toggleAgent,
    runCycle,
    emergency,
  } = useTradingContext();

  const ov = dash?.overview;
  const summ = active?.account_summary;
  const openCount = active?.active_trades?.length ?? dash?.survival?.open_positions_count ?? 0;
  const pendingApprovals = dash?.pending_approvals_count ?? 0;
  const exchangeName = (exchangeStatus?.exchange_id || dash?.account?.exchange || "binance").toUpperCase();
  const tradingMode = dash?.account?.trading_mode || "LIVE_READ_ONLY";
  const isLiveArmed = tradingMode === "LIVE_TRADING" && dash?.account?.live_trading_authorized;
  const isAgentRunning = Boolean(dash?.account?.is_trading_active);
  const isEmergencyHalted = Boolean(dash?.account?.emergency_stopped || dash?.survival?.emergency_stopped);

  const availUsdt = summ?.available_usdt ?? ov?.available_balance ?? 0;
  const equityUsdt = ov?.current_equity ?? 0;
  const marginUsed = summ?.total_margin_used_usd ?? ov?.locked_balance ?? 0;
  const unrealPnl = summ?.total_unrealized_pnl_usd ?? ov?.unrealized_pnl ?? 0;
  const realizedPnl = ov?.realized_pnl ?? 0;
  const drawdownPct = (ov?.current_drawdown_pct ?? 0) * 100;
  const maxDrawdownPct = (dash?.survival?.max_drawdown ?? 0.05) * 100;
  const kellyMultPct = (dash?.survival?.kelly_multiplier ?? 0.2) * 100;

  const nonZeroWallets = (portfolio?.balances || []).filter(
    (b) => b.total >= 0.0001 || b.asset === "USDT"
  );

  const needsFunding = Boolean(dash) && availUsdt <= 0;
  const needsArming =
    Boolean(dash) && (!dash?.account.live_trading_authorized || dash?.account.trading_mode !== "LIVE_TRADING");

  return (
    <div className="min-h-screen flex flex-col bg-[#080c12] text-slate-200">
      {/* =========================================================
          TIER 1: STICKY WEBSITE HEADER WITH ALL PAGE LINKS & CONTROLS
          ========================================================= */}
      <header className="sticky top-0 z-40 border-b border-[#1f2937] bg-[#0c1118]/95 backdrop-blur">
        <div className="mx-auto flex max-w-[1700px] flex-wrap items-center justify-between gap-3 px-4 py-2.5">
          {/* Left: Brand Identity */}
          <div className="flex items-center gap-3 shrink-0">
            <Link href="/" className="flex items-center gap-2 group">
              <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-amber-500/15 border border-amber-500/40 text-amber-400 group-hover:bg-amber-500/25 transition">
                <Zap className="h-4 w-4" />
              </span>
              <div className="leading-tight">
                <span className="text-sm font-black tracking-tight text-white">SurvivalX</span>
                <span className="block text-[10px] font-mono uppercase tracking-wider text-slate-400">
                  {exchangeName} USDⓈ-M FUTURES
                </span>
              </div>
            </Link>
          </div>

          {/* Center: Website Page Links */}
          <nav aria-label="Primary pages" className="flex items-center gap-1 overflow-x-auto">
            {NAV_ITEMS.map(({ href, label, icon: Icon }) => {
              const isActive = href === "/" ? pathname === "/" : pathname?.startsWith(href);
              return (
                <Link
                  key={href}
                  href={href}
                  className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-lg px-3 py-1.5 text-xs font-semibold transition ${
                    isActive
                      ? "bg-amber-500/15 text-amber-300 border border-amber-500/40"
                      : "text-slate-300 border border-transparent hover:bg-[#161f2c] hover:text-white"
                  }`}
                >
                  <Icon className="h-3.5 w-3.5 shrink-0" />
                  <span>{label}</span>
                  {href === "/trade" && openCount > 0 && (
                    <span className="rounded-full bg-emerald-500/20 border border-emerald-500/40 px-1.5 py-0.2 font-mono text-[10px] font-bold text-emerald-300">
                      {openCount}
                    </span>
                  )}
                  {href === "/dashboard" && pendingApprovals > 0 && (
                    <span className="rounded-full bg-amber-500/25 px-1.5 py-0.2 font-mono text-[10px] font-bold text-amber-300">
                      {pendingApprovals}
                    </span>
                  )}
                </Link>
              );
            })}
          </nav>

          {/* Right: Status Pills & Global Agent Actions */}
          <div className="flex flex-wrap items-center gap-2">
            <span
              className={`rounded-lg border px-2.5 py-1 text-[11px] font-bold tracking-wide whitespace-nowrap ${
                isLiveArmed
                  ? "border-emerald-500/45 bg-emerald-500/15 text-emerald-300"
                  : "border-amber-500/45 bg-amber-500/15 text-amber-300"
              }`}
            >
              {isLiveArmed ? "LIVE REAL-USDT" : "LIVE READ-ONLY"}
            </span>

            {dash?.survival?.state && <SurvivalBadge state={dash.survival.state} />}

            <button
              type="button"
              onClick={() => void toggleAgent()}
              disabled={busy !== null}
              className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-lg px-3 py-1.5 text-xs font-bold transition active:scale-[0.98] disabled:opacity-50 ${
                isAgentRunning
                  ? "bg-emerald-500 text-slate-950 hover:bg-emerald-400"
                  : "border border-[#2b3545] bg-[#161f2c] text-slate-100 hover:bg-[#1f2b3d]"
              }`}
            >
              {isAgentRunning ? <Square className="h-3 w-3 fill-current" /> : <Play className="h-3 w-3 fill-current" />}
              <span>{isAgentRunning ? "Agent Running · Pause" : "Start Agent"}</span>
            </button>

            <button
              type="button"
              onClick={() => void runCycle("15m")}
              disabled={busy !== null}
              className="inline-flex items-center gap-1.5 whitespace-nowrap rounded-lg border border-amber-500/40 bg-amber-500/15 px-3 py-1.5 text-xs font-bold text-amber-200 hover:bg-amber-500/25 transition active:scale-[0.98] disabled:opacity-50"
            >
              <Radar className="h-3.5 w-3.5" />
              <span>{busy === "cycle" ? "Scanning…" : "Scan & Trade Now"}</span>
            </button>

            <button
              type="button"
              onClick={() => void emergency()}
              disabled={busy !== null}
              className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-lg px-3 py-1.5 text-xs font-bold text-white transition active:scale-[0.98] disabled:opacity-50 ${
                isEmergencyHalted
                  ? "bg-sky-600 hover:bg-sky-500"
                  : "bg-rose-600 hover:bg-rose-500"
              }`}
            >
              <ShieldAlert className="h-3.5 w-3.5" />
              <span>{isEmergencyHalted ? "Reset Emergency" : "Emergency Stop"}</span>
            </button>
          </div>
        </div>
      </header>

      {/* =========================================================
          TIER 2: UNIFIED PERSISTENT ACCOUNT BALANCE & MARGIN STRIP
          ========================================================= */}
      <section className="border-b border-[#1f2937] bg-[#0e141d]">
        <div className="mx-auto flex max-w-[1700px] flex-wrap items-center justify-between gap-x-6 gap-y-2 px-4 py-2.5">
          <div className="flex flex-wrap items-center gap-x-6 gap-y-1.5">
            <BalanceStat
              label="Total Equity"
              value={ov ? `$${fmtUsd(equityUsdt)}` : "—"}
              sub={ov ? `Baseline $${fmtUsd(ov.initial_capital)}` : undefined}
              highlight
            />
            <BalanceStat
              label="Available USDT"
              value={dash ? `$${fmtUsd(availUsdt)}` : "—"}
              sub="Free Margin"
              valueClass={availUsdt > 0 ? "text-emerald-400" : "text-amber-300"}
            />
            <BalanceStat
              label="In Margin"
              value={dash ? `$${fmtUsd(marginUsed)}` : "—"}
              sub={`Positions ${openCount}/${dash?.survival?.max_open_positions ?? 3}`}
              valueClass="text-amber-300"
            />
            <BalanceStat
              label="Unrealized P&L"
              value={dash ? `${unrealPnl >= 0 ? "+" : ""}$${fmtUsd(unrealPnl, 4)}` : "—"}
              sub={summ ? `ROI ${summ.total_roi_pct >= 0 ? "+" : ""}${summ.total_roi_pct.toFixed(2)}%` : undefined}
              valueClass={unrealPnl >= 0 ? "text-emerald-400" : "text-rose-400"}
            />
            <BalanceStat
              label="Realized P&L"
              value={ov ? `${realizedPnl >= 0 ? "+" : ""}$${fmtUsd(realizedPnl, 4)}` : "—"}
              sub={ov ? `Fees $${fmtUsd(ov.total_trading_costs, 4)}` : undefined}
              valueClass={realizedPnl >= 0 ? "text-emerald-400" : "text-rose-400"}
            />
            <BalanceStat
              label="Drawdown / Risk"
              value={`-${drawdownPct.toFixed(2)}%`}
              sub={`Cap -${maxDrawdownPct.toFixed(0)}% · Kelly ${kellyMultPct.toFixed(0)}%`}
              valueClass={drawdownPct > 2.5 ? "text-amber-300" : "text-slate-200"}
            />
          </div>

          {/* Right side: Wallet Asset Pills & Exchange Mode Tag */}
          <div className="flex flex-wrap items-center gap-2">
            <span className="rounded-lg border border-amber-500/35 bg-amber-500/10 px-2.5 py-1 font-mono text-[11px] font-bold text-amber-300">
              ISOLATED · 1x DEFAULT (AUTO 2x–5x MICRO)
            </span>

            {nonZeroWallets.slice(0, 3).map((b) => (
              <span
                key={b.asset}
                className="inline-flex items-center gap-1.5 rounded-lg border border-[#222d3d] bg-[#080c12] px-2.5 py-1 text-[11px] font-mono"
              >
                <Wallet className="h-3 w-3 text-amber-400" />
                <span className="font-bold text-slate-300">{b.asset}</span>
                <span className="font-semibold text-white">
                  {b.asset === "USDT" ? `$${b.total.toFixed(2)}` : b.total.toFixed(5)}
                </span>
              </span>
            ))}

            <button
              type="button"
              onClick={() => void syncBalance()}
              disabled={busy !== null}
              title="Reconcile real-time balance with exchange Futures wallet"
              className="inline-flex items-center gap-1 rounded-lg border border-[#2b3545] bg-[#161f2c] px-2.5 py-1 text-[11px] font-semibold text-slate-200 hover:bg-[#1f2b3d] disabled:opacity-50"
            >
              <RefreshCw className={`h-3 w-3 ${busy === "sync-bal" ? "animate-spin" : ""}`} />
              <span>{busy === "sync-bal" ? "Syncing…" : "Sync Balance"}</span>
            </button>
          </div>
        </div>
      </section>

      {/* =========================================================
          TIER 3: CONTEXTUAL READINESS NOTICE (ONLY WHEN ACTION NEEDED)
          ========================================================= */}
      {(needsFunding || needsArming) && (
        <div className="border-b border-amber-500/35 bg-amber-500/10">
          <div className="mx-auto flex max-w-[1700px] flex-wrap items-center justify-between gap-3 px-4 py-2 text-xs text-amber-200">
            <div>
              {needsFunding ? (
                <>
                  <strong>Real-USDT Futures wallet is $0.00 USDT</strong> ({exchangeName} Futures needs any positive USDT balance to place live orders — fund your Futures wallet and click Sync Balance).
                </>
              ) : (
                <>
                  <strong>
                    Real-USDT Exchange Connected ({exchangeName} · ${availUsdt.toFixed(2)} USDT available)
                  </strong>{" "}
                  — Authorize <code>LIVE_TRADING</code> in Analytics &amp; API so agent clicks place real exchange orders.
                </>
              )}
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <button
                type="button"
                onClick={() => void syncBalance()}
                disabled={busy !== null}
                className="rounded-lg bg-amber-400 px-3 py-1 text-xs font-bold text-slate-950 hover:bg-amber-300 disabled:opacity-50"
              >
                {busy === "sync-bal" ? "Syncing…" : "Sync Live Balance"}
              </button>
              <Link
                href="/dashboard#exchange-setup"
                className="rounded-lg border border-amber-400/50 bg-[#161f2c] px-3 py-1 text-xs font-semibold text-amber-200 hover:bg-[#1f2b3d]"
              >
                {needsFunding ? "Exchange Settings" : "Arm Live Real-USDT"}
              </Link>
            </div>
          </div>
        </div>
      )}

      {/* =========================================================
          SHARED TOAST NOTIFICATION
          ========================================================= */}
      {toast && (
        <div
          className={`fixed bottom-4 right-4 z-50 max-w-md rounded-lg border px-4 py-2.5 text-xs font-semibold shadow-2xl ${
            toast.ok
              ? "border-emerald-500/50 bg-[#0d241b] text-emerald-200"
              : "border-rose-500/50 bg-[#271016] text-rose-200"
          }`}
        >
          {toast.msg}
        </div>
      )}

      {/* =========================================================
          MAIN ROUTE CONTENT
          ========================================================= */}
      <div className="flex-1">{children}</div>

      {/* =========================================================
          CONSISTENT WEBSITE FOOTER
          ========================================================= */}
      <footer className="border-t border-[#1f2937] bg-[#0c1118] px-4 py-3 text-[11px] text-slate-400">
        <div className="mx-auto flex max-w-[1700px] flex-wrap items-center justify-between gap-3">
          <span>
            <strong className="text-slate-200">SurvivalX</strong> · Futures Isolated · Fractional Kelly Risk Governor · Max 60m Hold · Trailing Profit Lock
          </span>
          <div className="flex items-center gap-4">
            <Link href="/" className="hover:text-amber-300 transition">
              Exchange
            </Link>
            <Link href="/trade" className="hover:text-amber-300 transition">
              Active Trade
            </Link>
            <Link href="/dashboard" className="hover:text-amber-300 transition">
              Analytics &amp; API
            </Link>
            <Link href="/guide" className="hover:text-amber-300 transition">
              Agent Guide
            </Link>
          </div>
        </div>
      </footer>
    </div>
  );
}

function BalanceStat({
  label,
  value,
  sub,
  valueClass = "text-white",
  highlight = false,
}: {
  label: string;
  value: string;
  sub?: string;
  valueClass?: string;
  highlight?: boolean;
}) {
  return (
    <div className="leading-tight">
      <div className="text-[10px] font-semibold uppercase tracking-wider text-slate-400">{label}</div>
      <div className={`font-mono ${highlight ? "text-base" : "text-sm"} font-bold ${valueClass}`}>
        {value}
      </div>
      {sub && <div className="text-[10px] text-slate-500 font-mono">{sub}</div>}
    </div>
  );
}
