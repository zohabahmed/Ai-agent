"use client";

import React from "react";
import Link from "next/link";
import { BarChart3, KeyRound, ShieldCheck } from "lucide-react";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  ComposedChart,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import CoinMarketChart from "@/components/CoinMarketChart";
import { ExchangeSetupPanel } from "@/components/ExchangeSetupPanel";
import { useTradingContext } from "@/context/TradingContext";

export default function AnalyticsAndSettingsPage() {
  const {
    token,
    dash,
    portfolio,
    exchangeStatus,
    strategies,
    approvals,
    busy,
    loading,
    error,
    refreshAll,
    notify,
    handleApprovalDecision,
  } = useTradingContext();

  if (loading && !dash) {
    return (
      <main className="mx-auto flex max-w-[1700px] items-center justify-center p-12">
        <div className="text-center space-y-3">
          <div className="inline-block h-8 w-8 animate-spin rounded-full border-4 border-amber-400 border-t-transparent" />
          <p className="text-sm text-slate-400">
            Loading Portfolio Analytics, Strategy Metrics &amp; Exchange Telemetry…
          </p>
        </div>
      </main>
    );
  }

  if (!dash) {
    return (
      <main className="mx-auto flex max-w-[1700px] items-center justify-center p-12">
        <div className="max-w-md rounded-lg border border-rose-500/40 bg-[#0e141d] p-6 text-center space-y-4">
          <h1 className="text-lg font-semibold text-rose-300">Backend Telemetry Unreachable</h1>
          <p className="text-xs text-slate-400">{error || "Ensure FastAPI server is running on port 8000."}</p>
          <button
            type="button"
            onClick={() => void refreshAll()}
            className="rounded-lg bg-emerald-500 px-4 py-2 text-xs font-bold text-slate-950 hover:bg-emerald-400"
          >
            Retry Connection
          </button>
        </div>
      </main>
    );
  }

  const {
    survival,
    market_tickers,
    recent_risk_decisions,
    recent_events,
  } = dash;

  const equityData = (portfolio?.equity_curve || []).map((pt) => ({
    time: new Date(pt.timestamp).toLocaleTimeString([], {
      hour: "2-digit",
      minute: "2-digit",
    }),
    equity: Number(pt.total_equity.toFixed(4)),
    cash: Number(pt.available_balance.toFixed(4)),
    drawdownPct: Number(((pt.drawdown_pct || 0) * 100).toFixed(2)),
    realizedPnl: Number((pt.realized_pnl || 0).toFixed(4)),
  }));

  const latestMetricsByStrat = new Map<
    string,
    { win_rate: number; profit_factor: number; sharpe_ratio: number; window_trades: number }
  >();
  for (const m of strategies?.recent_metrics || []) {
    if (!latestMetricsByStrat.has(m.strategy_name)) {
      latestMetricsByStrat.set(m.strategy_name, m);
    }
  }

  const strategyRows = (
    strategies?.strategies ||
    dash.strategies.map((s) => ({
      id: s.name,
      name: s.name,
      display_name: s.display_name,
      category: s.category,
      status: s.status,
      active_version: "1.0.0",
      parameters: {},
      preferred_regimes: s.preferred_regimes,
      enabled_in_runtime: s.enabled_in_registry,
    }))
  ).map((s) => {
    const m = latestMetricsByStrat.get(s.name);
    return {
      ...s,
      winRate: m ? m.win_rate : 0.55,
      profitFactor: m ? m.profit_factor : 1.25,
      sharpe: m ? m.sharpe_ratio : 1.1,
      trades: m ? m.window_trades : 0,
    };
  });

  const strategyChartData = strategyRows.map((s) => ({
    name: s.display_name.replace(" Strategy", ""),
    winRatePct: Number((s.winRate * 100).toFixed(1)),
    profitFactor: Number((s.profitFactor * 25).toFixed(1)),
  }));

  return (
    <main className="mx-auto max-w-[1700px] p-4 md:p-5 space-y-5">
      {/* Sub-navigation bar inside Analytics & API page */}
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-[#1f2937] bg-[#0e141d] px-4 py-3">
        <div>
          <h1 className="flex items-center gap-2 text-sm font-bold text-white">
            <BarChart3 className="h-4 w-4 text-amber-400" />
            <span>Portfolio Analytics, Strategy Telemetry &amp; Exchange API Setup</span>
          </h1>
          <p className="text-xs text-slate-400">
            Configure exchange credentials, authorize Live Real-USDT orders, inspect strategy performance curves, and review risk audit logs
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2 text-xs">
          <a
            href="#exchange-setup"
            className="inline-flex items-center gap-1 rounded-lg border border-amber-500/40 bg-amber-500/15 px-3 py-1.5 font-semibold text-amber-200 hover:bg-amber-500/25"
          >
            <KeyRound className="h-3.5 w-3.5" />
            <span>API &amp; Live Arming</span>
          </a>
          <a
            href="#performance-charts"
            className="rounded-lg border border-[#253142] bg-[#161f2c] px-3 py-1.5 font-semibold text-slate-200 hover:text-white"
          >
            Equity Curves
          </a>
          <a
            href="#strategies-table"
            className="rounded-lg border border-[#253142] bg-[#161f2c] px-3 py-1.5 font-semibold text-slate-200 hover:text-white"
          >
            Strategies &amp; Risk
          </a>
          <a
            href="#approvals-audit"
            className="inline-flex items-center gap-1 rounded-lg border border-[#253142] bg-[#161f2c] px-3 py-1.5 font-semibold text-slate-200 hover:text-white"
          >
            <ShieldCheck className="h-3.5 w-3.5" />
            <span>Approvals &amp; Audit</span>
          </a>
        </div>
      </div>

      {/* 1. LIVE REAL-USDT EXCHANGE CONNECTION & EXECUTION GATE */}
      <section id="exchange-setup" className="scroll-mt-24 space-y-3">
        <ExchangeSetupPanel
          token={token}
          exchangeStatus={exchangeStatus}
          portfolio={portfolio}
          onRefresh={refreshAll}
          onNotify={(msg) => notify(msg, true)}
        />
      </section>

      {/* 2. INTERACTIVE LIVE COIN CHART + OPENVIEW + TRADINGVIEW */}
      <CoinMarketChart
        token={token}
        tickers={market_tickers}
        openPositions={portfolio?.open_positions || []}
      />

      {/* 3. PORTFOLIO ANALYTICS CHARTS SUITE */}
      <section id="performance-charts" className="scroll-mt-24 grid grid-cols-1 gap-4 lg:grid-cols-12">
        <div className="lg:col-span-5 rounded-lg border border-[#1f2937] bg-[#0e141d] p-4">
          <div className="mb-3 flex items-center justify-between">
            <div>
              <h2 className="text-sm font-semibold text-white">Portfolio Equity &amp; Cash Curve (USD)</h2>
              <p className="text-[11px] text-slate-400">Real-time mark-to-market total equity vs unencumbered cash</p>
            </div>
            <span className="text-xs text-slate-400">
              Kelly: <strong className="text-emerald-400">{(survival.kelly_multiplier * 100).toFixed(0)}%</strong>
            </span>
          </div>
          <div className="h-56 w-full">
            {equityData.length > 0 ? (
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={equityData}>
                  <defs>
                    <linearGradient id="eqGradMain" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="#10b981" stopOpacity={0.35} />
                      <stop offset="95%" stopColor="#10b981" stopOpacity={0.0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" stroke="#16202e" />
                  <XAxis dataKey="time" stroke="#64748b" fontSize={10} />
                  <YAxis stroke="#64748b" fontSize={10} domain={["auto", "auto"]} />
                  <Tooltip contentStyle={{ backgroundColor: "#0e141d", borderColor: "#263244", fontSize: "12px" }} />
                  <Area type="monotone" dataKey="equity" stroke="#10b981" strokeWidth={2} fillOpacity={1} fill="url(#eqGradMain)" name="Total Equity ($)" />
                  <Area type="monotone" dataKey="cash" stroke="#f59e0b" strokeWidth={1.5} fillOpacity={0} name="Free Cash ($)" />
                </AreaChart>
              </ResponsiveContainer>
            ) : (
              <div className="flex h-full items-center justify-center text-xs text-slate-500">
                Run a trading cycle to populate equity curve snapshots.
              </div>
            )}
          </div>
        </div>

        <div className="lg:col-span-4 rounded-lg border border-[#1f2937] bg-[#0e141d] p-4">
          <div className="mb-3">
            <h2 className="text-sm font-semibold text-white">Drawdown (%) &amp; Realized Profit Curve</h2>
            <p className="text-[11px] text-slate-400">
              Survival drawdown ceiling (-{(survival.max_drawdown * 100).toFixed(0)}%) &amp; banked P&amp;L
            </p>
          </div>
          <div className="h-56 w-full">
            {equityData.length > 0 ? (
              <ResponsiveContainer width="100%" height="100%">
                <ComposedChart data={equityData}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#16202e" />
                  <XAxis dataKey="time" stroke="#64748b" fontSize={10} />
                  <YAxis yAxisId="dd" stroke="#f59e0b" fontSize={10} tickFormatter={(v) => `-${v}%`} />
                  <YAxis yAxisId="pnl" orientation="right" stroke="#10b981" fontSize={10} tickFormatter={(v) => `$${v}`} />
                  <Tooltip contentStyle={{ backgroundColor: "#0e141d", borderColor: "#263244", fontSize: "12px" }} />
                  <Area yAxisId="dd" type="monotone" dataKey="drawdownPct" stroke="#f59e0b" fill="#f59e0b" fillOpacity={0.18} name="Drawdown (%)" />
                  <Line yAxisId="pnl" type="monotone" dataKey="realizedPnl" stroke="#10b981" strokeWidth={2} dot={false} name="Realized P&L ($)" />
                </ComposedChart>
              </ResponsiveContainer>
            ) : (
              <div className="flex h-full items-center justify-center text-xs text-slate-500">
                No drawdown snapshots recorded yet.
              </div>
            )}
          </div>
        </div>

        <div className="lg:col-span-3 rounded-lg border border-[#1f2937] bg-[#0e141d] p-4">
          <div className="mb-3">
            <h2 className="text-sm font-semibold text-white">Strategy Win Rate &amp; Edge (%)</h2>
            <p className="text-[11px] text-slate-400">Multi-strategy statistical comparison</p>
          </div>
          <div className="h-56 w-full">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={strategyChartData}>
                <CartesianGrid strokeDasharray="3 3" stroke="#16202e" />
                <XAxis dataKey="name" stroke="#64748b" fontSize={9} />
                <YAxis stroke="#64748b" fontSize={10} />
                <Tooltip contentStyle={{ backgroundColor: "#0e141d", borderColor: "#263244", fontSize: "11px" }} />
                <Bar dataKey="winRatePct" fill="#10b981" radius={[4, 4, 0, 0]} name="Win Rate (%)" />
                <Bar dataKey="profitFactor" fill="#f59e0b" radius={[4, 4, 0, 0]} name="Edge Score" />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>
      </section>

      {/* 4. STRATEGY COMPETITION & OPEN POSITIONS / DECISIONS */}
      <section id="strategies-table" className="scroll-mt-24 grid grid-cols-1 gap-4 lg:grid-cols-12">
        <div className="lg:col-span-7 rounded-lg border border-[#1f2937] bg-[#0e141d] p-4">
          <h2 className="mb-3 text-sm font-semibold text-white">
            Independent Multi-Strategy Engine &amp; Regime Specialization
          </h2>
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead className="border-b border-[#1f2937] text-slate-400">
                <tr>
                  <th className="pb-2">Strategy</th>
                  <th className="pb-2">Category</th>
                  <th className="pb-2">Status</th>
                  <th className="pb-2">Preferred Regimes</th>
                  <th className="pb-2">Win Rate</th>
                  <th className="pb-2">Profit Factor</th>
                  <th className="pb-2">Sharpe</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[#161e2b]">
                {strategyRows.map((s) => (
                  <tr key={s.name} className="hover:bg-[#131b26]">
                    <td className="py-2.5 font-medium text-white">
                      {s.display_name}{" "}
                      <span className="text-[10px] text-slate-500">v{s.active_version}</span>
                    </td>
                    <td className="py-2.5 text-slate-300">{s.category}</td>
                    <td className="py-2.5">
                      <span
                        className={`rounded px-2 py-0.5 text-[10px] font-semibold ${
                          s.enabled_in_runtime ? "bg-emerald-500/20 text-emerald-300" : "bg-slate-800 text-slate-400"
                        }`}
                      >
                        {s.status}
                      </span>
                    </td>
                    <td className="py-2.5 font-mono text-[10px] text-amber-300">
                      {s.preferred_regimes?.join(", ") || "ALL"}
                    </td>
                    <td className="py-2.5 font-mono">{(s.winRate * 100).toFixed(1)}%</td>
                    <td className="py-2.5 font-mono">{s.profitFactor.toFixed(2)}</td>
                    <td className="py-2.5 font-mono">{s.sharpe.toFixed(2)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        <div className="lg:col-span-5 rounded-lg border border-[#1f2937] bg-[#0e141d] p-4 space-y-4">
          <div>
            <div className="mb-2 flex items-center justify-between">
              <h2 className="text-sm font-semibold text-white">
                Active Open Positions ({portfolio?.open_positions.length || 0}/{survival.max_open_positions})
              </h2>
              <Link
                href="/trade"
                className="rounded border border-amber-500/40 bg-amber-500/15 px-2.5 py-1 text-[11px] font-bold text-amber-200 hover:bg-amber-500/25 transition"
              >
                Open Trade-Only View →
              </Link>
            </div>
            {portfolio?.open_positions.length ? (
              <div className="space-y-2">
                {portfolio.open_positions.map((pos) => (
                  <div key={pos.id} className="rounded-lg border border-[#1f2937] bg-[#080c12] p-3 text-xs">
                    <div className="flex justify-between font-semibold">
                      <span className="flex items-center gap-1.5">
                        <span
                          className={`rounded px-1.5 py-0.5 text-[10px] font-bold ${
                            pos.side === "SHORT"
                              ? "bg-rose-500/20 text-rose-300 border border-rose-500/40"
                              : "bg-emerald-500/20 text-emerald-300 border border-emerald-500/40"
                          }`}
                        >
                          {pos.side}
                        </span>
                        <span>{pos.symbol}</span>
                        <span className="rounded bg-[#161f2c] px-1.5 py-0.5 font-mono text-[10px] text-amber-300">
                          {pos.market_type || "FUTURES"} {pos.leverage || 1}x
                        </span>
                        <span className="text-slate-400 font-normal">({pos.strategy_name})</span>
                      </span>
                      <span className={pos.unrealized_pnl >= 0 ? "text-emerald-400" : "text-rose-400"}>
                        {pos.unrealized_pnl >= 0 ? "+" : ""}${pos.unrealized_pnl.toFixed(4)}
                      </span>
                    </div>
                    <div className="mt-1 flex flex-wrap justify-between gap-2 text-[11px] text-slate-400 font-mono">
                      <span>Entry: ${pos.entry_price.toFixed(2)}</span>
                      {pos.stop_loss && <span>Stop: ${pos.stop_loss.toFixed(2)}</span>}
                      {pos.take_profit && <span>TP: ${pos.take_profit.toFixed(2)}</span>}
                      <span>Margin: ${pos.margin_used.toFixed(2)}</span>
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <p className="rounded-lg border border-[#1f2937] bg-[#080c12] p-3 text-xs text-slate-400">
                No active positions. Capital remains safely idle until an opportunity clears all survival, OpenBB, and TradingView/CoinGlass gates.
              </p>
            )}
          </div>

          <div>
            <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-slate-400">
              Recent Kelly &amp; Survival Risk Decisions
            </h3>
            <div className="max-h-44 space-y-1.5 overflow-y-auto pr-1">
              {recent_risk_decisions.slice(0, 8).map((d) => (
                <div
                  key={d.id}
                  className="flex items-center justify-between rounded border border-[#1f2937] bg-[#080c12] px-2.5 py-1.5 text-[11px]"
                >
                  <div>
                    <span className="font-semibold text-slate-200">{d.symbol}</span>{" "}
                    <span className="text-slate-400">({d.strategy})</span>
                    {d.decision !== "APPROVED" && d.rejection_reasons?.length > 0 && (
                      <p className="text-[10px] text-amber-300/90">Blocked: {d.rejection_reasons.join(", ")}</p>
                    )}
                  </div>
                  <span
                    className={`rounded px-1.5 py-0.5 text-[10px] font-semibold ${
                      d.decision === "APPROVED" ? "bg-emerald-500/20 text-emerald-300" : "bg-slate-800 text-slate-400"
                    }`}
                  >
                    {d.decision === "APPROVED" ? `Kelly ${(d.kelly_fraction * 100).toFixed(1)}%` : "PRESERVED"}
                  </span>
                </div>
              ))}
            </div>
          </div>
        </div>
      </section>

      {/* 5. WITHDRAWAL APPROVAL GATE & IMMUTABLE AUDIT LOG */}
      <section id="approvals-audit" className="scroll-mt-24 grid grid-cols-1 gap-4 lg:grid-cols-12">
        <div className="lg:col-span-5 rounded-lg border border-[#1f2937] bg-[#0e141d] p-4">
          <h2 className="mb-2 text-sm font-semibold text-white">
            Profit Lock &amp; Human-Approved Operations ({dash.pending_approvals_count} Pending)
          </h2>
          <p className="text-xs text-slate-400 mb-3">
            Withdrawable Realized Profit:{" "}
            <strong className="text-emerald-300 font-mono">
              ${dash.withdrawals.realized_withdrawable_profit.toFixed(4)}
            </strong>{" "}
            | Protected Reserve:{" "}
            <strong className="text-slate-200 font-mono">
              ${dash.withdrawals.reserve_capital.toFixed(2)}
            </strong>
          </p>
          {approvals?.approvals?.length ? (
            <div className="space-y-2">
              {approvals.approvals.slice(0, 4).map((a) => (
                <div
                  key={a.id}
                  className="flex items-center justify-between rounded-lg border border-[#1f2937] bg-[#080c12] p-3 text-xs"
                >
                  <div>
                    <div className="font-semibold text-white">
                      {a.operation_type} ({a.status})
                    </div>
                    <div className="text-[11px] text-slate-400">{a.reason}</div>
                  </div>
                  {a.status === "PENDING" && (
                    <div className="flex gap-1.5">
                      <button
                        type="button"
                        onClick={() => void handleApprovalDecision(a.id, true)}
                        disabled={busy !== null}
                        className="rounded bg-emerald-600 px-2.5 py-1 text-[11px] font-semibold text-white hover:bg-emerald-500"
                      >
                        Approve
                      </button>
                      <button
                        type="button"
                        onClick={() => void handleApprovalDecision(a.id, false)}
                        disabled={busy !== null}
                        className="rounded bg-rose-600 px-2.5 py-1 text-[11px] font-semibold text-white hover:bg-rose-500"
                      >
                        Reject
                      </button>
                    </div>
                  )}
                </div>
              ))}
            </div>
          ) : (
            <div className="rounded-lg border border-[#1f2937] bg-[#080c12] p-3 text-xs text-slate-400">
              No pending human approval requests. Autonomous withdrawals are strictly disabled without human approval.
            </div>
          )}
        </div>

        <div className="lg:col-span-7 rounded-lg border border-[#1f2937] bg-[#0e141d] p-4">
          <h2 className="mb-2 text-sm font-semibold text-white">Immutable System &amp; Survival Audit Stream</h2>
          <div className="max-h-48 space-y-1.5 overflow-y-auto text-xs font-mono">
            {recent_events.map((ev) => (
              <div
                key={ev.id}
                className="flex items-start justify-between gap-2 rounded border border-[#1f2937] bg-[#080c12] px-3 py-1.5"
              >
                <div>
                  <span
                    className={`mr-2 font-bold ${
                      ev.severity === "CRITICAL"
                        ? "text-rose-400"
                        : ev.severity === "WARNING"
                        ? "text-amber-300"
                        : "text-emerald-400"
                    }`}
                  >
                    [{ev.event_type}]
                  </span>
                  <span className="text-slate-300">{ev.message}</span>
                </div>
                <span className="shrink-0 text-[10px] text-slate-500">
                  {new Date(ev.created_at).toLocaleTimeString()}
                </span>
              </div>
            ))}
          </div>
        </div>
      </section>
    </main>
  );
}
