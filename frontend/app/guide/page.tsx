"use client";

import React from "react";
import Link from "next/link";
import {
  Activity,
  BarChart3,
  BookOpen,
  CandlestickChart,
  Cpu,
  Gauge,
  KeyRound,
  ShieldCheck,
  Timer,
  TrendingUp,
} from "lucide-react";
import { useTradingContext } from "@/context/TradingContext";

const PIPELINE_STEPS = [
  {
    step: "01",
    title: "Live Balance & Integrity Sync",
    desc: "Reconciles your exchange Futures wallet (USDT available, margin, open orders) before scanning. Any stale feed, DB error, or unverified balance immediately blocks entries.",
  },
  {
    step: "02",
    title: "Global Multi-Coin Discovery (15m + 5m)",
    desc: "Fetches live tickers across Futures markets, scans up to 64 active volume movers + pinned coins + your open positions, and supports searching/charting any coin on demand.",
  },
  {
    step: "03",
    title: "Market Regime Classification",
    desc: "Classifies each coin into BULLISH_TRENDING, BEARISH_TRENDING, SIDEWAYS, LOW/HIGH_VOLATILITY, VOLATILITY_EXPANSION/CONTRACTION, or ABNORMAL_UNSTABLE.",
  },
  {
    step: "04",
    title: "4-Strategy Competition & Quantitative Confirmation",
    desc: "Trend Following, Mean Reversion, Momentum, and Volatility Breakout compete per coin. Candidates pass OpenBB Quant metrics, TradingView 13-indicator confluence, and Futures funding/L-S sentiment.",
  },
  {
    step: "05",
    title: "Statistical Edge, Survival Governor & Kelly Sizing",
    desc: "Computes Wilson lower-bound win probability (min sample size), net Expected Value after fees/spread/slippage, and Fractional Kelly risk budget (capped at 1.5% risk/trade, 5% max drawdown).",
  },
  {
    step: "06",
    title: "Isolated Futures Execution & Fast Profit Lock",
    desc: "Places isolated Futures orders (1x default, automatic 2x–5x only if a small balance needs to clear a venue order floor), arms a trailing lock at +0.45%, targets +1.5% TP / -0.6% SL, and hard-closes by 60 min.",
  },
];

const STRATEGIES = [
  {
    name: "Trend Following",
    regimes: "BULLISH_TRENDING · BEARISH_TRENDING · VOLATILITY_EXPANSION",
    signals: "EMA 20 / EMA 50 alignment, RSI filter, MACD histogram momentum, ADX >= 18, ATR stops.",
  },
  {
    name: "Mean Reversion",
    regimes: "SIDEWAYS · LOW_VOLATILITY · VOLATILITY_CONTRACTION",
    signals: "Bollinger Bands (20, 2.0), Z-Score <= -1.35 / >= +1.35, RSI oversold/overbought extremes.",
  },
  {
    name: "Momentum Breakout",
    regimes: "BULLISH_TRENDING · VOLATILITY_EXPANSION",
    signals: "Fast vs slow Rate-of-Change acceleration, persistence bars, and >= 1.05x volume expansion.",
  },
  {
    name: "Volatility Breakout",
    regimes: "VOLATILITY_CONTRACTION · VOLATILITY_EXPANSION · LOW_VOLATILITY",
    signals: "Bollinger squeeze compression breaking into ATR expansion while rejecting extreme outlier spikes.",
  },
];

const SURVIVAL_STATES = [
  { state: "NORMAL", scale: "1.00x Exposure", rule: "Healthy equity curve and stable regimes; standard Fractional Kelly limits active." },
  { state: "CAUTION", scale: "0.65x Exposure", rule: "Near drawdown/daily-loss threshold or HIGH_VOLATILITY; tightens win-rate filter (>=50%)." },
  { state: "DEFENSIVE", scale: "0.50x Exposure", rule: "Strategy drift or decay detected; halves sizing and demands stricter Expected Value." },
  { state: "RECOVERY", scale: "0.50x Exposure", rule: "Post-halt operator reset state; rebuilds confidence gradually before returning to NORMAL." },
  { state: "HALTED", scale: "0.00x (Blocked)", rule: "Emergency Stop, max drawdown (-5%), max daily loss (-1%), ABNORMAL_UNSTABLE market, or outage." },
];

export default function AgentGuidePage() {
  const { dash } = useTradingContext();
  const sv = dash?.survival;

  return (
    <main className="mx-auto max-w-[1700px] p-4 md:p-6 space-y-6">
      {/* Intro Hero Card */}
      <section className="rounded-lg border border-[#1f2937] bg-[#0e141d] p-5 md:p-6">
        <div className="flex flex-col justify-between gap-4 lg:flex-row lg:items-center">
          <div className="max-w-3xl space-y-2">
            <div className="inline-flex items-center gap-2 rounded-md border border-amber-500/40 bg-amber-500/10 px-2.5 py-1 text-[11px] font-bold uppercase tracking-wider text-amber-300">
              <BookOpen className="h-3.5 w-3.5" />
              <span>SurvivalX Platform Guide &amp; Architecture Summary</span>
            </div>
            <h1 className="text-xl md:text-2xl font-black tracking-tight text-white">
              Autonomous Multi-Coin Futures Agent Built Around Capital Preservation
            </h1>
            <p className="text-xs md:text-sm text-slate-300 leading-relaxed">
              SurvivalX scans live Futures pairs globally, tests setups through statistical confluence and Net Expected Value after fees, sizes positions with Fractional Kelly Criterion, enforces an independent Survival Governor, and exits fast inside a 60-minute window.
            </p>
          </div>

          <div className="flex flex-wrap gap-2 shrink-0">
            <Link
              href="/"
              className="inline-flex items-center gap-1.5 rounded-lg bg-amber-400 px-3.5 py-2 text-xs font-bold text-slate-950 hover:bg-amber-300"
            >
              <CandlestickChart className="h-3.5 w-3.5" />
              <span>Go to Exchange</span>
            </Link>
            <Link
              href="/trade"
              className="inline-flex items-center gap-1.5 rounded-lg border border-[#253142] bg-[#161f2c] px-3.5 py-2 text-xs font-semibold text-white hover:bg-[#1f2b3d]"
            >
              <Activity className="h-3.5 w-3.5" />
              <span>Active Trade</span>
            </Link>
            <Link
              href="/dashboard#exchange-setup"
              className="inline-flex items-center gap-1.5 rounded-lg border border-emerald-500/40 bg-emerald-500/15 px-3.5 py-2 text-xs font-bold text-emerald-200 hover:bg-emerald-500/25"
            >
              <KeyRound className="h-3.5 w-3.5" />
              <span>Configure API &amp; Go Live</span>
            </Link>
          </div>
        </div>

        {/* Live Configured Limits Strip */}
        <div className="mt-5 grid grid-cols-2 gap-3 border-t border-[#1f2937] pt-4 sm:grid-cols-3 lg:grid-cols-6 font-mono text-xs">
          <RuleMetric label="Kelly Mode / Mult" value={`${sv?.kelly_mode || "NORMAL"} (${((sv?.kelly_multiplier ?? 0.2) * 100).toFixed(0)}%)`} />
          <RuleMetric label="Max Risk / Trade" value={`${((sv?.max_risk_per_trade ?? 0.015) * 100).toFixed(2)}%`} />
          <RuleMetric label="Daily Loss Limit" value={`-${((sv?.max_daily_loss ?? 0.01) * 100).toFixed(2)}%`} />
          <RuleMetric label="Max Drawdown Cap" value={`-${((sv?.max_drawdown ?? 0.05) * 100).toFixed(0)}%`} />
          <RuleMetric label="Max Concurrent Trades" value={`${sv?.max_open_positions ?? 3} Futures`} />
          <RuleMetric label="Margin / Hold Limit" value="Isolated · 60 min" />
        </div>
      </section>

      {/* 6-Step Execution Loop */}
      <section className="space-y-3">
        <h2 className="flex items-center gap-2 text-sm font-bold uppercase tracking-wider text-slate-300">
          <Cpu className="h-4 w-4 text-amber-400" />
          <span>End-to-End Autonomous Decision Pipeline</span>
        </h2>
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2 lg:grid-cols-3">
          {PIPELINE_STEPS.map((item) => (
            <div key={item.step} className="rounded-lg border border-[#1f2937] bg-[#0e141d] p-4 space-y-1.5">
              <div className="flex items-center gap-2">
                <span className="rounded bg-amber-500/15 border border-amber-500/40 px-2 py-0.5 font-mono text-xs font-bold text-amber-300">
                  {item.step}
                </span>
                <h3 className="text-sm font-bold text-white">{item.title}</h3>
              </div>
              <p className="text-xs text-slate-400 leading-relaxed">{item.desc}</p>
            </div>
          ))}
        </div>
      </section>

      {/* Strategies + Fast Exit Rules */}
      <section className="grid grid-cols-1 gap-4 lg:grid-cols-12">
        <div className="lg:col-span-7 rounded-lg border border-[#1f2937] bg-[#0e141d] p-5 space-y-3">
          <h2 className="flex items-center gap-2 text-sm font-bold text-white">
            <TrendingUp className="h-4 w-4 text-emerald-400" />
            <span>4 Competing Trading Strategies</span>
          </h2>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            {STRATEGIES.map((s) => (
              <div key={s.name} className="rounded-lg border border-[#1f2937] bg-[#080c12] p-3.5 space-y-1.5">
                <div className="text-xs font-bold text-white">{s.name}</div>
                <div className="font-mono text-[10px] text-amber-300">{s.regimes}</div>
                <p className="text-[11px] text-slate-400 leading-relaxed">{s.signals}</p>
              </div>
            ))}
          </div>
        </div>

        <div className="lg:col-span-5 rounded-lg border border-[#1f2937] bg-[#0e141d] p-5 space-y-3">
          <h2 className="flex items-center gap-2 text-sm font-bold text-white">
            <Timer className="h-4 w-4 text-amber-400" />
            <span>Fast Profit-Booking &amp; Any-Balance Rules</span>
          </h2>
          <ul className="space-y-2.5 text-xs text-slate-300">
            <li className="rounded-lg border border-[#1f2937] bg-[#080c12] p-3">
              <strong className="text-emerald-300">Trailing Profit Lock (+0.45%):</strong> Once a trade moves +0.45% in your favor, Stop-Loss ratchets to breakeven+fees (+0.25%) and trails peak pullbacks while letting winners run toward the +1.5% Take-Profit.
            </li>
            <li className="rounded-lg border border-[#1f2937] bg-[#080c12] p-3">
              <strong className="text-amber-300">30m / 60m Time Exit:</strong> After 30 minutes any fee-covering gain (+0.18%) is booked cleanly; every trade is strictly closed by the 60-minute hard cap (or anytime manually via Book 25% / Book 50% / Stop).
            </li>
            <li className="rounded-lg border border-[#1f2937] bg-[#080c12] p-3">
              <strong className="text-white">Any-Positive-Balance Support:</strong> Keeps 1x isolated margin by default; if your Futures wallet ($4.95, $2+) sits below an exchange minimum order floor (such as Binance USD-M $5 contracts), it automatically applies the lowest isolated multiple (2x–5x), or runs native $1+ at 1x on MEXC / KuCoin Futures / Gate.
            </li>
          </ul>
        </div>
      </section>

      {/* Survival FSM + Page Map */}
      <section className="grid grid-cols-1 gap-4 lg:grid-cols-12">
        <div className="lg:col-span-7 rounded-lg border border-[#1f2937] bg-[#0e141d] p-5 space-y-3">
          <h2 className="flex items-center gap-2 text-sm font-bold text-white">
            <ShieldCheck className="h-4 w-4 text-emerald-400" />
            <span>Independent Survival Governor States</span>
          </h2>
          <div className="space-y-2">
            {SURVIVAL_STATES.map((item) => (
              <div
                key={item.state}
                className="flex flex-col gap-1 rounded-lg border border-[#1f2937] bg-[#080c12] px-3.5 py-2.5 sm:flex-row sm:items-center sm:justify-between"
              >
                <div className="flex items-center gap-2.5">
                  <span className="w-24 shrink-0 font-mono text-xs font-bold text-amber-300">{item.state}</span>
                  <span className="text-xs text-slate-300">{item.rule}</span>
                </div>
                <span className="shrink-0 font-mono text-[11px] font-bold text-slate-200">{item.scale}</span>
              </div>
            ))}
          </div>
        </div>

        <div className="lg:col-span-5 rounded-lg border border-[#1f2937] bg-[#0e141d] p-5 space-y-3">
          <h2 className="flex items-center gap-2 text-sm font-bold text-white">
            <Gauge className="h-4 w-4 text-amber-400" />
            <span>Pages Inside This Platform</span>
          </h2>
          <div className="space-y-2 text-xs">
            <PageSummaryLink
              href="/"
              icon={<CandlestickChart className="h-4 w-4 text-amber-400" />}
              title="Exchange Terminal (/)"
              desc="Global coin scanner, any-coin search & pinning, interactive chart, isolated Futures order ticket, and open/closed tabs."
            />
            <PageSummaryLink
              href="/trade"
              icon={<Activity className="h-4 w-4 text-emerald-400" />}
              title="Active Trade (/trade)"
              desc="Dedicated live position telemetry (ROI, Liq Price, Margin Ratio, Price Ladder, SL/TP editing) and Why-Taken Dossier."
            />
            <PageSummaryLink
              href="/dashboard"
              icon={<BarChart3 className="h-4 w-4 text-amber-400" />}
              title="Analytics & API (/dashboard)"
              desc="Equity & drawdown curves, strategy leaderboard, API key encryption setup, LIVE_TRADING authorization, and audit logs."
            />
          </div>
        </div>
      </section>
    </main>
  );
}

function RuleMetric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-[#1f2937] bg-[#080c12] p-2.5">
      <div className="text-[10px] uppercase tracking-wider text-slate-500">{label}</div>
      <div className="mt-0.5 font-bold text-white">{value}</div>
    </div>
  );
}

function PageSummaryLink({
  href,
  icon,
  title,
  desc,
}: {
  href: string;
  icon: React.ReactNode;
  title: string;
  desc: string;
}) {
  return (
    <Link
      href={href}
      className="flex items-start gap-3 rounded-lg border border-[#1f2937] bg-[#080c12] p-3 hover:border-amber-500/40 transition"
    >
      <div className="mt-0.5 shrink-0">{icon}</div>
      <div>
        <div className="font-bold text-white">{title}</div>
        <p className="mt-0.5 text-[11px] text-slate-400">{desc}</p>
      </div>
    </Link>
  );
}
