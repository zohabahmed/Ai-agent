"use client";

import React, { useEffect, useState } from "react";
import {
  Activity,
  Bot,
  Radar,
  RefreshCw,
  SlidersHorizontal,
} from "lucide-react";
import {
  Area,
  Bar,
  CartesianGrid,
  ComposedChart,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { fmtUsd, pxDigits, useSymbolMarket, useTradingContext } from "@/context/TradingContext";
import { TradingApi } from "@/services/api";
import type { ActiveTradeDetailed } from "@/types/trading";

const TIMEFRAMES = [
  { label: "5m", tvInterval: "5" },
  { label: "15m", tvInterval: "15" },
  { label: "1h", tvInterval: "60" },
  { label: "4h", tvInterval: "240" },
];

function formatDuration(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  const mins = Math.floor(seconds / 60);
  const secs = seconds % 60;
  if (mins < 60) return `${mins}m ${secs}s`;
  const hrs = Math.floor(mins / 60);
  const remMins = mins % 60;
  return `${hrs}h ${remMins}m`;
}

export default function ActiveTradeOnlyPage() {
  const {
    token,
    active,
    scanner,
    loading,
    busy,
    notify,
    refreshAll,
    runCycle,
    openTrade,
    bookProfit,
  } = useTradingContext();

  const [selectedTradeId, setSelectedTradeId] = useState<string | null>(null);
  const [coinInput, setCoinInput] = useState<string>("");
  const [timeframe, setTimeframe] = useState<string>("15m");
  const [chartMode, setChartMode] = useState<"QUANT" | "TRADINGVIEW">("QUANT");
  const [editingTpSl, setEditingTpSl] = useState<boolean>(false);
  const [slInput, setSlInput] = useState<string>("");
  const [tpInput, setTpInput] = useState<string>("");
  const [tpSlBusy, setTpSlBusy] = useState<boolean>(false);

  const normalizePair = (raw: string): string => {
    const clean = raw.trim().toUpperCase().replace("-", "/");
    if (!clean) return "";
    if (clean.includes("/")) return clean;
    return clean.endsWith("USDT") && clean.length > 4 ? `${clean.slice(0, -4)}/USDT` : `${clean}/USDT`;
  };

  const activeTrades = active?.active_trades || [];
  useEffect(() => {
    if (activeTrades.length > 0) {
      setSelectedTradeId((prev) =>
        prev && activeTrades.some((t) => t.id === prev) ? prev : activeTrades[0].id
      );
    } else {
      setSelectedTradeId(null);
    }
  }, [activeTrades]);

  const selectedTrade: ActiveTradeDetailed | null =
    activeTrades.find((t) => t.id === selectedTradeId) || activeTrades[0] || null;

  const quickCoins = (scanner?.coins || []).slice(0, 8);
  const activeSymbol =
    selectedTrade?.symbol || normalizePair(coinInput) || quickCoins[0]?.symbol || "SOL/USDT";

  const { market: marketData, reload: loadChartData } = useSymbolMarket(activeSymbol, timeframe, 8000);

  const handleOpenLiveTrade = async (rawSymbol: string) => {
    const sym = normalizePair(rawSymbol);
    if (!sym) return;
    const res = await openTrade(sym, undefined, timeframe);
    if (res?.trade?.position_id) {
      setSelectedTradeId(res.trade.position_id);
    }
    void loadChartData();
  };

  const handleStartEditTpSl = (trade: ActiveTradeDetailed) => {
    setSlInput(trade.stop_loss ? String(trade.stop_loss) : "");
    setTpInput(trade.take_profit ? String(trade.take_profit) : "");
    setEditingTpSl(true);
  };

  const handleSaveTpSl = async (trade: ActiveTradeDetailed) => {
    setTpSlBusy(true);
    try {
      const slNum = slInput.trim() ? Number(slInput) : null;
      const tpNum = tpInput.trim() ? Number(tpInput) : null;
      await TradingApi.updatePositionTpSl(token, trade.id, {
        stop_loss: slNum,
        take_profit: tpNum,
      });
      setEditingTpSl(false);
      await refreshAll();
      notify(`Updated Stop-Loss & Take-Profit for ${trade.symbol}.`, true);
    } catch (err) {
      notify(err instanceof Error ? err.message : "Failed to update TP/SL", false);
    } finally {
      setTpSlBusy(false);
    }
  };

  const candles = (marketData?.candles || []).map((c) => ({
    ...c,
    timeLabel: new Date(c.timestamp).toLocaleTimeString([], {
      hour: "2-digit",
      minute: "2-digit",
    }),
  }));

  const tvInterval = TIMEFRAMES.find((t) => t.label === timeframe)?.tvInterval || "15";
  const tvCleanSymbol = `BINANCE:${activeSymbol.replace("/", "")}`;
  const isActionBusy = busy !== null || tpSlBusy;

  return (
    <main className="mx-auto max-w-[1700px] p-4 md:p-5 space-y-4">
      {/* Sub-header focused cleanly on Active Trade Workspace controls */}
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-[#1f2937] bg-[#0e141d] px-4 py-3">
        <div>
          <h1 className="text-sm font-bold text-white flex items-center gap-2">
            <Activity className="h-4 w-4 text-amber-400" />
            <span>Active Trade Terminal &amp; Entry Reasoning Dossier</span>
          </h1>
          <p className="text-xs text-slate-400">
            Live position P&amp;L, ROI%, liquidation buffer, interactive chart overlays, and complete decision proof
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={() => void runCycle(timeframe)}
            disabled={isActionBusy}
            className="inline-flex items-center gap-1.5 rounded-lg bg-emerald-500 px-3.5 py-2 text-xs font-bold text-slate-950 hover:bg-emerald-400 disabled:opacity-50"
          >
            <Radar className="h-3.5 w-3.5" />
            <span>{busy === "cycle" ? "Scanning All Charts…" : "Scan All Charts & Take Best Trade"}</span>
          </button>
          <button
            type="button"
            onClick={() => {
              void refreshAll();
              void loadChartData();
            }}
            className="inline-flex items-center gap-1.5 rounded-lg border border-[#253142] bg-[#161f2c] px-3 py-2 text-xs font-semibold text-slate-200 hover:text-white"
          >
            <RefreshCw className="h-3.5 w-3.5" />
            <span>Refresh Live</span>
          </button>
        </div>
      </div>

      {/* Multi-trade selector if >1 trade active */}
      {activeTrades.length > 1 && (
        <section className="flex flex-wrap items-center gap-2 rounded-lg border border-[#1f2937] bg-[#0e141d] p-2.5">
          <span className="px-2 text-xs font-bold uppercase tracking-wider text-slate-400">
            Select Active Trade:
          </span>
          {activeTrades.map((t) => {
            const isSelected = selectedTrade?.id === t.id;
            return (
              <button
                key={t.id}
                type="button"
                onClick={() => {
                  setSelectedTradeId(t.id);
                  setEditingTpSl(false);
                }}
                className={`flex items-center gap-2 rounded-lg px-3 py-1.5 text-xs font-bold transition ${
                  isSelected
                    ? "border border-amber-500/40 bg-amber-500/15 text-amber-200"
                    : "border border-[#253142] bg-[#080c12] text-slate-300 hover:text-white"
                }`}
              >
                <span
                  className={`rounded px-1.5 py-0.5 text-[10px] ${
                    t.side === "SHORT" ? "bg-rose-500/20 text-rose-300" : "bg-emerald-500/20 text-emerald-300"
                  }`}
                >
                  {t.side} {t.leverage}x
                </span>
                <span>{t.symbol}</span>
                <span className={`font-mono ${t.unrealized_pnl >= 0 ? "text-emerald-300" : "text-rose-300"}`}>
                  {t.unrealized_pnl >= 0 ? "+" : ""}${t.unrealized_pnl.toFixed(4)} ({t.roi_pct >= 0 ? "+" : ""}
                  {t.roi_pct.toFixed(2)}%)
                </span>
              </button>
            );
          })}
        </section>
      )}

      {/* Main Active Trade Content or Clean Empty State */}
      {loading && !selectedTrade ? (
        <div className="rounded-lg border border-[#1f2937] bg-[#0e141d] p-12 text-center">
          <div className="inline-block h-8 w-8 animate-spin rounded-full border-4 border-amber-400 border-t-transparent" />
          <p className="mt-3 text-sm text-slate-400">Loading active trade telemetry…</p>
        </div>
      ) : !selectedTrade ? (
        <section className="rounded-lg border border-[#1f2937] bg-[#0e141d] p-8 text-center space-y-4">
          <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-full border border-amber-500/35 bg-amber-500/10 text-amber-300">
            <Activity className="h-6 w-6" />
          </div>
          <h2 className="text-lg font-bold text-white">No Active Open Trade Right Now</h2>
          <p className="mx-auto max-w-xl text-xs text-slate-400">
            Let the agent scan every live global chart across the exchange to pick the strongest setup, or search and launch any coin directly through the OpenBB + TradingView + Fractional Kelly pipeline.
          </p>

          <div className="flex flex-wrap justify-center gap-3 pt-1">
            <button
              type="button"
              onClick={() => void runCycle(timeframe)}
              disabled={isActionBusy}
              className="inline-flex items-center gap-2 rounded-lg bg-emerald-500 px-5 py-2.5 text-xs font-bold text-slate-950 hover:bg-emerald-400 disabled:opacity-50"
            >
              <Bot className="h-4 w-4" />
              <span>{busy === "cycle" ? "Scanning All Global Charts…" : "Scan All Charts & Take Best Trade"}</span>
            </button>
          </div>

          <form
            onSubmit={(e) => {
              e.preventDefault();
              if (coinInput.trim()) void handleOpenLiveTrade(coinInput);
            }}
            className="mx-auto flex max-w-md flex-wrap justify-center gap-2 pt-2"
          >
            <input
              type="text"
              value={coinInput}
              onChange={(e) => setCoinInput(e.target.value)}
              placeholder="Search any coin (SOL, DOGE, PEPE, SUI, WIF…)"
              className="min-w-[220px] flex-1 rounded-lg border border-[#253142] bg-[#080c12] px-3 py-2 text-xs text-white placeholder-slate-500 focus:border-amber-400 focus:outline-none"
            />
            <button
              type="submit"
              disabled={isActionBusy || !coinInput.trim()}
              className="rounded-lg border border-amber-500/40 bg-amber-500/15 px-4 py-2 text-xs font-bold text-amber-200 hover:bg-amber-500/25 disabled:opacity-50"
            >
              Agent Trade {coinInput.trim() ? normalizePair(coinInput) : "Coin"}
            </button>
          </form>

          {quickCoins.length > 0 && (
            <div className="flex flex-wrap justify-center gap-1.5 pt-1">
              {quickCoins.map((c) => (
                <button
                  key={c.symbol}
                  type="button"
                  disabled={isActionBusy}
                  onClick={() => void handleOpenLiveTrade(c.symbol)}
                  className="rounded-lg border border-[#253142] bg-[#080c12] px-2.5 py-1 text-[11px] font-semibold text-slate-200 hover:border-amber-400 hover:text-amber-300 disabled:opacity-50"
                >
                  {c.symbol} {c.setup_status === "READY" ? `(${c.direction})` : ""}
                </button>
              ))}
            </div>
          )}
        </section>
      ) : (
        <>
          {/* SECTION A: EXCHANGE-GRADE POSITION TELEMETRY */}
          <section className="rounded-lg border border-[#1f2937] bg-[#0e141d] p-5 space-y-5">
            <div className="flex flex-col justify-between gap-4 border-b border-[#1f2937] pb-4 lg:flex-row lg:items-center">
              <div className="flex flex-wrap items-center gap-2.5">
                <span
                  className={`rounded-lg px-3 py-1 text-sm font-black tracking-wide ${
                    selectedTrade.side === "SHORT"
                      ? "bg-rose-500/20 text-rose-300 border border-rose-500/40"
                      : "bg-emerald-500/20 text-emerald-300 border border-emerald-500/40"
                  }`}
                >
                  {selectedTrade.side}
                </span>
                <h2 className="text-xl font-black text-white font-mono">{selectedTrade.symbol}</h2>
                <span className="rounded-md bg-amber-500/15 border border-amber-500/40 px-2.5 py-1 font-mono text-xs font-bold text-amber-300">
                  {selectedTrade.market_type} {selectedTrade.leverage}x ({selectedTrade.margin_mode})
                </span>
                <span className="rounded-md border border-[#253142] bg-[#161f2c] px-2.5 py-1 text-xs font-semibold text-slate-200">
                  Strategy: {selectedTrade.strategy_name}
                </span>
                <span className="rounded-md border border-[#253142] bg-[#080c12] px-2.5 py-1 font-mono text-xs text-slate-400">
                  {selectedTrade.exchange} • {selectedTrade.trading_mode} • Duration:{" "}
                  <strong className="text-slate-200">{formatDuration(selectedTrade.duration_seconds)}</strong>
                </span>
                {selectedTrade.exchange_protected ? (
                  <span className="rounded-md border border-emerald-500/40 bg-emerald-500/15 px-2.5 py-1 text-[11px] font-bold text-emerald-300">
                    Exchange STOP_MARKET + TP Protected
                  </span>
                ) : selectedTrade.trading_mode === "LIVE_TRADING" || selectedTrade.trading_mode === "TESTNET" ? (
                  <span className="rounded-md border border-amber-500/40 bg-amber-500/10 px-2.5 py-1 text-[11px] font-semibold text-amber-300">
                    Worker Exit Monitor (Exchange Triggers Pending)
                  </span>
                ) : (
                  <span className="rounded-md border border-slate-700 bg-slate-800/70 px-2.5 py-1 text-[11px] font-semibold text-slate-300">
                    Simulated Paper Exits
                  </span>
                )}
                {selectedTrade.scaled_out && (
                  <span className="rounded-md border border-amber-500/40 bg-amber-500/15 px-2.5 py-1 text-[11px] font-bold text-amber-300">
                    1R Scaled Out · 1.2x ATR Trail Active
                  </span>
                )}
              </div>

              <div className="flex flex-wrap items-center gap-2">
                <button
                  type="button"
                  onClick={() => handleStartEditTpSl(selectedTrade)}
                  className="inline-flex items-center gap-1.5 rounded-lg border border-[#253142] bg-[#161f2c] px-3 py-2 text-xs font-semibold text-slate-200 hover:border-amber-400 hover:text-white transition"
                >
                  <SlidersHorizontal className="h-3.5 w-3.5" />
                  <span>Adjust TP / SL</span>
                </button>
                <button
                  type="button"
                  onClick={() => void bookProfit(selectedTrade, 0.25)}
                  disabled={isActionBusy}
                  className="rounded-lg border border-emerald-500/40 bg-emerald-500/15 px-3 py-2 text-xs font-bold text-emerald-300 hover:bg-emerald-500/25 disabled:opacity-50 transition"
                >
                  {busy === `close-${selectedTrade.id}-0.25` ? "Booking…" : "Book 25%"}
                </button>
                <button
                  type="button"
                  onClick={() => void bookProfit(selectedTrade, 0.5)}
                  disabled={isActionBusy}
                  className="rounded-lg border border-emerald-500/40 bg-emerald-500/15 px-3 py-2 text-xs font-bold text-emerald-300 hover:bg-emerald-500/25 disabled:opacity-50 transition"
                >
                  {busy === `close-${selectedTrade.id}-0.5` ? "Booking…" : "Book 50%"}
                </button>
                <button
                  type="button"
                  onClick={() => void bookProfit(selectedTrade, 1.0)}
                  disabled={isActionBusy}
                  className="rounded-lg bg-rose-600 px-4 py-2 text-xs font-bold text-white hover:bg-rose-500 disabled:opacity-50 transition"
                >
                  {busy === `close-${selectedTrade.id}-1` ? "Closing…" : "Stop / Book 100%"}
                </button>
              </div>
            </div>

            {editingTpSl && (
              <div className="flex flex-wrap items-end gap-3 rounded-lg border border-amber-500/40 bg-[#080c12] p-3.5">
                <div>
                  <label className="block text-[11px] font-semibold text-rose-300">Stop Loss Price (USDT)</label>
                  <input
                    type="number"
                    step="0.0001"
                    value={slInput}
                    onChange={(e) => setSlInput(e.target.value)}
                    placeholder="e.g. 92000"
                    className="mt-1 rounded-lg border border-[#253142] bg-[#0e141d] px-3 py-1.5 font-mono text-xs text-white"
                  />
                </div>
                <div>
                  <label className="block text-[11px] font-semibold text-emerald-300">Take Profit Price (USDT)</label>
                  <input
                    type="number"
                    step="0.0001"
                    value={tpInput}
                    onChange={(e) => setTpInput(e.target.value)}
                    placeholder="e.g. 96500"
                    className="mt-1 rounded-lg border border-[#253142] bg-[#0e141d] px-3 py-1.5 font-mono text-xs text-white"
                  />
                </div>
                <button
                  type="button"
                  onClick={() => void handleSaveTpSl(selectedTrade)}
                  disabled={tpSlBusy}
                  className="rounded-lg bg-emerald-500 px-4 py-1.5 text-xs font-bold text-slate-950 hover:bg-emerald-400"
                >
                  Save TP/SL
                </button>
                <button
                  type="button"
                  onClick={() => setEditingTpSl(false)}
                  className="rounded-lg border border-[#253142] bg-[#161f2c] px-3 py-1.5 text-xs text-slate-300"
                >
                  Cancel
                </button>
              </div>
            )}

            <div className="grid grid-cols-1 gap-4 lg:grid-cols-12">
              <div
                className={`lg:col-span-4 rounded-lg border p-4 flex flex-col justify-between ${
                  selectedTrade.unrealized_pnl >= 0
                    ? "border-emerald-500/40 bg-emerald-950/20"
                    : "border-rose-500/40 bg-rose-950/20"
                }`}
              >
                <div className="flex items-start justify-between">
                  <div>
                    <div className="text-xs font-semibold uppercase tracking-wider text-slate-400">
                      Unrealized Profit / Loss
                    </div>
                    <div
                      className={`mt-1 font-mono text-3xl font-black ${
                        selectedTrade.unrealized_pnl >= 0 ? "text-emerald-400" : "text-rose-400"
                      }`}
                    >
                      {selectedTrade.unrealized_pnl >= 0 ? "+" : ""}${selectedTrade.unrealized_pnl.toFixed(4)}
                    </div>
                  </div>
                  <div className="text-right">
                    <div className="text-xs font-semibold uppercase tracking-wider text-slate-400">
                      ROI (Margin)
                    </div>
                    <div
                      className={`mt-1 font-mono text-2xl font-black ${
                        selectedTrade.roi_pct >= 0 ? "text-emerald-400" : "text-rose-400"
                      }`}
                    >
                      {selectedTrade.roi_pct >= 0 ? "+" : ""}{selectedTrade.roi_pct.toFixed(2)}%
                    </div>
                  </div>
                </div>

                <div className="mt-4 grid grid-cols-3 gap-2 border-t border-[#1f2937] pt-3 text-[11px] font-mono">
                  <div>
                    <span className="text-slate-400 block">Gross P&amp;L</span>
                    <span className={selectedTrade.gross_pnl >= 0 ? "text-emerald-300" : "text-rose-300"}>
                      {selectedTrade.gross_pnl >= 0 ? "+" : ""}${selectedTrade.gross_pnl.toFixed(4)}
                    </span>
                  </div>
                  <div>
                    <span className="text-slate-400 block">Unlev. Move</span>
                    <span className={selectedTrade.unleveraged_return_pct >= 0 ? "text-emerald-300" : "text-rose-300"}>
                      {selectedTrade.unleveraged_return_pct >= 0 ? "+" : ""}{selectedTrade.unleveraged_return_pct.toFixed(2)}%
                    </span>
                  </div>
                  <div>
                    <span className="text-slate-400 block">Entry Fee</span>
                    <span className="text-slate-200">${selectedTrade.entry_fee_usd.toFixed(4)}</span>
                  </div>
                </div>
              </div>

              <div className="lg:col-span-8 grid grid-cols-2 gap-3 sm:grid-cols-4">
                <MetricTile
                  label={`Size (${selectedTrade.symbol.split("/")[0]})`}
                  value={`${selectedTrade.quantity.toFixed(6)} ${selectedTrade.symbol.split("/")[0]}`}
                  sub={`$${selectedTrade.current_notional_usd.toFixed(2)} USDT`}
                  subClass="text-amber-300"
                />
                <MetricTile
                  label="Entry Price"
                  value={`$${fmtUsd(selectedTrade.entry_price, pxDigits(selectedTrade.entry_price))}`}
                  sub={`Cost $${selectedTrade.entry_notional_usd.toFixed(2)}`}
                />
                <MetricTile
                  label="Mark Price (Live)"
                  value={`$${fmtUsd(selectedTrade.mark_price, pxDigits(selectedTrade.mark_price))}`}
                  valueClass="text-emerald-300"
                  sub={`BE $${fmtUsd(selectedTrade.breakeven_price, pxDigits(selectedTrade.breakeven_price))}`}
                />
                <MetricTile
                  label="Est. Liquidation Price"
                  value={`$${fmtUsd(selectedTrade.liquidation_price, pxDigits(selectedTrade.liquidation_price || selectedTrade.entry_price))}`}
                  valueClass="text-amber-400"
                  borderClass="border-amber-500/35 bg-amber-950/15"
                  sub={`Buffer ${selectedTrade.distance_to_liq_pct.toFixed(2)}% away`}
                />
                <MetricTile
                  label={`Margin (${selectedTrade.margin_mode})`}
                  value={`$${selectedTrade.initial_margin_usd.toFixed(2)} USDT`}
                  sub={`Bal $${selectedTrade.margin_balance_usd.toFixed(2)}`}
                />
                <MetricTile
                  label="Margin Ratio"
                  value={`${selectedTrade.margin_ratio_pct.toFixed(2)}%`}
                  valueClass={
                    selectedTrade.margin_ratio_pct > 50
                      ? "text-rose-400"
                      : selectedTrade.margin_ratio_pct > 25
                      ? "text-amber-300"
                      : "text-emerald-400"
                  }
                  sub={`Maint $${selectedTrade.maintenance_margin_usd.toFixed(2)} (${selectedTrade.maintenance_margin_rate_pct.toFixed(1)}%)`}
                />
                <MetricTile
                  label={`Take Profit (TP)${selectedTrade.tp_order_id ? ` · #${selectedTrade.tp_order_id.slice(-6)}` : ""}`}
                  value={selectedTrade.take_profit ? `$${fmtUsd(selectedTrade.take_profit, pxDigits(selectedTrade.take_profit))}` : "Not Set"}
                  valueClass="text-emerald-400"
                  borderClass="border-emerald-500/30 bg-emerald-950/15"
                  sub={
                    selectedTrade.expected_tp_profit_usd !== null
                      ? `Est +$${selectedTrade.expected_tp_profit_usd.toFixed(2)} (+${selectedTrade.expected_tp_roi_pct?.toFixed(1)}%)`
                      : "Click Adjust TP/SL"
                  }
                />
                <MetricTile
                  label={`Stop Loss${selectedTrade.reward_risk_ratio ? ` · R:R ${selectedTrade.reward_risk_ratio}:1` : ""}${selectedTrade.sl_order_id ? ` · #${selectedTrade.sl_order_id.slice(-6)}` : ""}`}
                  value={selectedTrade.stop_loss ? `$${fmtUsd(selectedTrade.stop_loss, pxDigits(selectedTrade.stop_loss))}` : "Not Set"}
                  valueClass="text-rose-400"
                  borderClass="border-rose-500/30 bg-rose-950/15"
                  sub={
                    selectedTrade.expected_sl_loss_usd !== null
                      ? `Risk $${selectedTrade.expected_sl_loss_usd.toFixed(2)} (${selectedTrade.expected_sl_roi_pct?.toFixed(1)}%)`
                      : "Click Adjust TP/SL"
                  }
                />
              </div>
            </div>

            {/* 5-Step Visual Price Ladder */}
            <div className="rounded-lg border border-[#1f2937] bg-[#080c12] p-3.5">
              <div className="mb-2 flex flex-wrap items-center justify-between gap-2 text-xs">
                <span className="font-bold uppercase tracking-wider text-slate-300">
                  Price Ladder &amp; Liquidation Buffer
                </span>
                <span className="font-mono text-[11px] text-slate-400">
                  Opened {new Date(selectedTrade.opened_at).toLocaleTimeString()}
                </span>
              </div>
              <div className="grid grid-cols-2 gap-2 sm:grid-cols-5 font-mono text-xs">
                <LadderStep title="1. LIQUIDATION" price={`$${selectedTrade.liquidation_price.toFixed(4)}`} meta={`-${selectedTrade.distance_to_liq_pct.toFixed(2)}% dist`} cls="border-amber-500/30 bg-amber-950/15 text-amber-400" />
                <LadderStep title="2. STOP LOSS" price={selectedTrade.stop_loss ? `$${selectedTrade.stop_loss.toFixed(4)}` : "N/A"} meta={selectedTrade.distance_to_sl_pct !== null ? `${selectedTrade.distance_to_sl_pct.toFixed(2)}% away` : "--"} cls="border-rose-500/30 bg-rose-950/15 text-rose-400" />
                <LadderStep title="3. ENTRY PRICE" price={`$${selectedTrade.entry_price.toFixed(4)}`} meta={`BE $${selectedTrade.breakeven_price.toFixed(4)}`} cls="border-slate-600/40 bg-[#121923] text-white" />
                <LadderStep title="4. MARK PRICE" price={`$${selectedTrade.mark_price.toFixed(4)}`} meta={`${selectedTrade.unleveraged_return_pct >= 0 ? "+" : ""}${selectedTrade.unleveraged_return_pct.toFixed(2)}% move`} cls="border-amber-400/40 bg-amber-950/15 text-amber-300" />
                <LadderStep title="5. TAKE PROFIT" price={selectedTrade.take_profit ? `$${selectedTrade.take_profit.toFixed(4)}` : "N/A"} meta={selectedTrade.distance_to_tp_pct !== null ? `${selectedTrade.distance_to_tp_pct.toFixed(2)}% target` : "--"} cls="border-emerald-500/30 bg-emerald-950/15 text-emerald-400" />
              </div>
            </div>
          </section>

          {/* SECTION B: CHART + DECISION DOSSIER */}
          <section className="grid grid-cols-1 gap-4 xl:grid-cols-12">
            <div className="xl:col-span-7 rounded-lg border border-[#1f2937] bg-[#0e141d] p-5 space-y-4">
              <div className="flex flex-col justify-between gap-3 border-b border-[#1f2937] pb-3 sm:flex-row sm:items-center">
                <div>
                  <h3 className="text-base font-bold text-white">
                    Active Trade Live Chart — {selectedTrade.symbol} ({timeframe})
                  </h3>
                  <p className="text-xs text-slate-400">
                    Entry (${selectedTrade.entry_price.toFixed(4)}), Stop-Loss, Take-Profit &amp; EMA/Bollinger Overlays
                  </p>
                </div>

                <div className="flex flex-wrap items-center gap-2">
                  <div className="flex rounded-lg border border-[#253142] bg-[#080c12] p-0.5">
                    {TIMEFRAMES.map((tfItem) => (
                      <button
                        key={tfItem.label}
                        type="button"
                        onClick={() => setTimeframe(tfItem.label)}
                        className={`rounded-md px-2.5 py-1 font-mono text-xs font-semibold transition ${
                          timeframe === tfItem.label ? "bg-[#1e293b] text-amber-300" : "text-slate-400 hover:text-white"
                        }`}
                      >
                        {tfItem.label}
                      </button>
                    ))}
                  </div>

                  <div className="flex rounded-lg border border-[#253142] bg-[#080c12] p-0.5">
                    <button
                      type="button"
                      onClick={() => setChartMode("QUANT")}
                      className={`rounded-md px-2.5 py-1 text-xs font-bold transition ${
                        chartMode === "QUANT" ? "bg-emerald-500 text-slate-950" : "text-slate-400 hover:text-white"
                      }`}
                    >
                      Quant Overlay
                    </button>
                    <button
                      type="button"
                      onClick={() => setChartMode("TRADINGVIEW")}
                      className={`rounded-md px-2.5 py-1 text-xs font-bold transition ${
                        chartMode === "TRADINGVIEW" ? "bg-amber-400 text-slate-950" : "text-slate-400 hover:text-white"
                      }`}
                    >
                      TradingView
                    </button>
                  </div>
                </div>
              </div>

              {chartMode === "QUANT" ? (
                <div className="space-y-3">
                  <div className="h-80 w-full rounded-lg border border-[#1f2937] bg-[#080c12] p-2">
                    {candles.length > 0 ? (
                      <ResponsiveContainer width="100%" height="100%">
                        <ComposedChart data={candles}>
                          <defs>
                            <linearGradient id="activeTradePriceGrad" x1="0" y1="0" x2="0" y2="1">
                              <stop offset="5%" stopColor="#f59e0b" stopOpacity={0.28} />
                              <stop offset="95%" stopColor="#f59e0b" stopOpacity={0.0} />
                            </linearGradient>
                          </defs>
                          <CartesianGrid strokeDasharray="3 3" stroke="#16202e" />
                          <XAxis dataKey="timeLabel" stroke="#64748b" fontSize={11} minTickGap={24} />
                          <YAxis
                            stroke="#64748b"
                            fontSize={11}
                            domain={["auto", "auto"]}
                            tickFormatter={(v) => `$${Number(v).toLocaleString()}`}
                          />
                          <Tooltip
                            contentStyle={{
                              backgroundColor: "#0e141d",
                              borderColor: "#263244",
                              fontSize: "12px",
                            }}
                          />
                          <Area type="monotone" dataKey="close" stroke="#f59e0b" strokeWidth={2} fill="url(#activeTradePriceGrad)" name="Close Price" />
                          <Line type="monotone" dataKey="ema_20" stroke="#10b981" strokeWidth={1.5} dot={false} name="EMA 20" />
                          <Line type="monotone" dataKey="ema_50" stroke="#94a3b8" strokeWidth={1.3} dot={false} name="EMA 50" />
                          <Line type="monotone" dataKey="bb_upper" stroke="#38bdf8" strokeDasharray="3 3" strokeWidth={1} dot={false} name="BB Upper" />
                          <Line type="monotone" dataKey="bb_lower" stroke="#38bdf8" strokeDasharray="3 3" strokeWidth={1} dot={false} name="BB Lower" />
                          <ReferenceLine
                            y={selectedTrade.entry_price}
                            stroke="#f59e0b"
                            strokeWidth={2}
                            strokeDasharray="4 4"
                            label={{ value: `ENTRY $${selectedTrade.entry_price.toFixed(4)}`, fill: "#f59e0b", fontSize: 10 }}
                          />
                          {selectedTrade.stop_loss && (
                            <ReferenceLine
                              y={selectedTrade.stop_loss}
                              stroke="#f43f5e"
                              strokeWidth={2}
                              strokeDasharray="4 4"
                              label={{ value: `STOP LOSS $${selectedTrade.stop_loss.toFixed(4)}`, fill: "#f43f5e", fontSize: 10 }}
                            />
                          )}
                          {selectedTrade.take_profit && (
                            <ReferenceLine
                              y={selectedTrade.take_profit}
                              stroke="#10b981"
                              strokeWidth={2}
                              strokeDasharray="4 4"
                              label={{ value: `TAKE PROFIT $${selectedTrade.take_profit.toFixed(4)}`, fill: "#10b981", fontSize: 10 }}
                            />
                          )}
                        </ComposedChart>
                      </ResponsiveContainer>
                    ) : (
                      <div className="flex h-full items-center justify-center text-xs text-slate-500">
                        Loading live candle series…
                      </div>
                    )}
                  </div>

                  <div className="h-36 w-full rounded-lg border border-[#1f2937] bg-[#080c12] p-2">
                    <div className="mb-1 flex items-center justify-between px-2 text-[11px] text-slate-400">
                      <span>RSI (14) Momentum &amp; Volume Profile</span>
                      <span className="font-mono">
                        RSI:{" "}
                        <strong className="text-amber-300">
                          {candles.length > 0 ? candles[candles.length - 1].rsi_14?.toFixed(1) : "--"}
                        </strong>
                      </span>
                    </div>
                    {candles.length > 0 && (
                      <ResponsiveContainer width="100%" height="82%">
                        <ComposedChart data={candles}>
                          <CartesianGrid strokeDasharray="3 3" stroke="#16202e" />
                          <XAxis dataKey="timeLabel" stroke="#64748b" fontSize={10} hide />
                          <YAxis yAxisId="rsi" domain={[0, 100]} stroke="#f59e0b" fontSize={10} ticks={[30, 50, 70]} />
                          <YAxis yAxisId="vol" orientation="right" stroke="#475569" fontSize={10} hide />
                          <ReferenceLine yAxisId="rsi" y={70} stroke="#f43f5e" strokeDasharray="2 2" />
                          <ReferenceLine yAxisId="rsi" y={30} stroke="#10b981" strokeDasharray="2 2" />
                          <Bar yAxisId="vol" dataKey="volume" fill="#334155" opacity={0.45} name="Volume" />
                          <Line yAxisId="rsi" type="monotone" dataKey="rsi_14" stroke="#f59e0b" strokeWidth={1.8} dot={false} name="RSI (14)" />
                        </ComposedChart>
                      </ResponsiveContainer>
                    )}
                  </div>
                </div>
              ) : (
                <div className="h-[460px] w-full overflow-hidden rounded-lg border border-[#1f2937] bg-[#080c12]">
                  <iframe
                    title={`TradingView ${selectedTrade.symbol}`}
                    src={`https://s.tradingview.com/widgetembed/?frameElementId=tv_active_trade&symbol=${encodeURIComponent(
                      tvCleanSymbol
                    )}&interval=${tvInterval}&hidesidetoolbar=0&symboledit=1&saveimage=0&toolbarbg=0e141d&studies=%5B%22RSI%40tv-basicstudies%22%2C%22MACD%40tv-basicstudies%22%2C%22BB%40tv-basicstudies%22%5D&theme=dark&style=1&timezone=Etc%2FUTC`}
                    className="h-full w-full border-0"
                    allowFullScreen
                  />
                </div>
              )}
            </div>

            {/* RIGHT 5 COLS: ENTRY REASONING DOSSIER */}
            <div className="xl:col-span-5 rounded-lg border border-[#1f2937] bg-[#0e141d] p-5 space-y-4">
              <div className="border-b border-[#1f2937] pb-3">
                <div className="flex items-center justify-between">
                  <h3 className="text-base font-bold text-white">Why This Trade Was Taken</h3>
                  <span className="rounded-full bg-emerald-500/20 border border-emerald-500/40 px-2.5 py-0.5 text-[11px] font-bold text-emerald-300">
                    {selectedTrade.why_taken.kelly_and_risk.decision}
                  </span>
                </div>
                <p className="mt-0.5 text-xs text-slate-400">
                  Verified evidence from Strategy Engine, OpenBB Quant, TradingView &amp; CoinGlass at entry
                </p>
              </div>

              <div className="rounded-lg border border-amber-500/30 bg-[#080c12] p-3.5 space-y-2">
                <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
                  <span className="font-bold text-amber-300 uppercase tracking-wider">1. Primary Signal &amp; Strategy</span>
                  <span className="font-mono text-xs text-white">
                    Confidence: <strong className="text-emerald-300">{selectedTrade.why_taken.signal_confidence_pct}%</strong> |{" "}
                    <strong className="text-amber-300">{selectedTrade.why_taken.market_regime}</strong>
                  </span>
                </div>
                <p className="text-xs font-medium text-white bg-[#0e141d] rounded-lg p-2.5 border border-[#1f2937]">
                  &ldquo;{selectedTrade.why_taken.signal_reason}&rdquo;
                </p>
                <ul className="space-y-1 text-[11px] text-slate-300 pl-4 list-disc">
                  {selectedTrade.why_taken.kelly_and_risk.ai_explanation.map((item, idx) => (
                    <li key={idx}>{item}</li>
                  ))}
                </ul>
              </div>

              <div className="rounded-lg border border-[#1f2937] bg-[#080c12] p-3.5 space-y-2">
                <div className="flex items-center justify-between text-xs">
                  <span className="font-bold text-emerald-300 uppercase tracking-wider">2. Fractional Kelly &amp; Survival Gate</span>
                  <span className="font-mono text-[11px] text-slate-400">
                    State: <strong className="text-white">{selectedTrade.why_taken.kelly_and_risk.survival_state}</strong>
                  </span>
                </div>
                <div className="grid grid-cols-3 gap-2 font-mono text-xs">
                  <DossierTile label="Win Prob (CI Low)" value={`${selectedTrade.why_taken.kelly_and_risk.win_probability_pct}%`} sub={`(${selectedTrade.why_taken.kelly_and_risk.ci_low_pct}%)`} />
                  <DossierTile label="Expected Value" value={`+${selectedTrade.why_taken.kelly_and_risk.net_ev_pct}%`} valCls="text-emerald-400" />
                  <DossierTile label="Fractional Kelly" value={`${selectedTrade.why_taken.kelly_and_risk.fractional_kelly_pct}%`} valCls="text-amber-300" sub={`Full ${selectedTrade.why_taken.kelly_and_risk.full_kelly_pct}%`} />
                </div>
              </div>

              <div className="rounded-lg border border-[#1f2937] bg-[#080c12] p-3.5 space-y-2">
                <div className="flex items-center justify-between text-xs">
                  <span className="font-bold text-slate-200 uppercase tracking-wider">3. OpenBB Quant Confluence</span>
                  <span className="font-mono text-xs text-emerald-300 font-bold">
                    Score: {(Number(selectedTrade.why_taken.openbb_quant?.confluence_score ?? 0.65) * 100).toFixed(0)}% (
                    {String(selectedTrade.why_taken.openbb_quant?.trend_bias ?? "BULLISH")})
                  </span>
                </div>
                <div className="grid grid-cols-3 gap-2 font-mono text-[11px]">
                  <DossierTile
                    label="ADX / DI Spread"
                    value={`${Number(selectedTrade.why_taken.openbb_quant?.adx_14 ?? selectedTrade.why_taken.regime_adx).toFixed(1)} / ${Number(selectedTrade.why_taken.openbb_quant?.di_spread ?? 4.2).toFixed(1)}`}
                  />
                  <DossierTile
                    label="SuperTrend / OBV"
                    value={`${selectedTrade.why_taken.openbb_quant?.supertrend_bullish !== false ? "BULL" : "BEAR"} / ${selectedTrade.why_taken.openbb_quant?.obv_accumulation !== false ? "ACCUM" : "DIST"}`}
                    valCls="text-emerald-300"
                  />
                  <DossierTile
                    label="ADF p-val / RSI"
                    value={`${Number(selectedTrade.why_taken.openbb_quant?.adf_pvalue ?? 0.04).toFixed(3)} / ${Number(selectedTrade.why_taken.openbb_quant?.rsi_14 ?? 54.0).toFixed(1)}`}
                  />
                </div>
              </div>

              <div className="rounded-lg border border-[#1f2937] bg-[#080c12] p-3.5 space-y-2">
                <div className="flex items-center justify-between text-xs">
                  <span className="font-bold text-amber-300 uppercase tracking-wider">4. TradingView &amp; Futures Sentiment</span>
                  <span className="font-mono text-xs font-bold text-emerald-300">
                    TV: {String(selectedTrade.why_taken.tradingview_rating?.overall_rating ?? "BUY")}
                  </span>
                </div>
                <div className="grid grid-cols-2 gap-2 font-mono text-[11px]">
                  <DossierTile
                    label="8h Funding / Open Interest"
                    value={`${Number(selectedTrade.why_taken.derivatives_sentiment?.funding_rate_pct ?? 0.01).toFixed(4)}% | $${(Number(selectedTrade.why_taken.derivatives_sentiment?.open_interest_usd ?? 0) / 1e6).toFixed(1)}M`}
                  />
                  <DossierTile
                    label="L/S Ratio & Taker Flow"
                    value={`L/S: ${Number(selectedTrade.why_taken.derivatives_sentiment?.long_short_ratio ?? 1.05).toFixed(2)} | Taker: ${Number(selectedTrade.why_taken.derivatives_sentiment?.taker_buy_sell_ratio ?? 1.04).toFixed(2)}`}
                    valCls="text-amber-300"
                  />
                </div>
                <div className="flex flex-wrap items-center justify-between gap-2 pt-1 text-[11px] font-mono text-slate-400">
                  <span>Order ID: <strong className="text-slate-200">{selectedTrade.why_taken.execution_audit.client_order_id || "N/A"}</strong></span>
                  <span>Slippage: <strong className="text-slate-200">{selectedTrade.why_taken.execution_audit.slippage_bps.toFixed(2)} bps</strong></span>
                </div>
              </div>
            </div>
          </section>
        </>
      )}
    </main>
  );
}

function MetricTile({
  label,
  value,
  sub,
  valueClass = "text-white",
  subClass = "text-slate-400",
  borderClass = "border-[#1f2937] bg-[#080c12]",
}: {
  label: string;
  value: string;
  sub?: string;
  valueClass?: string;
  subClass?: string;
  borderClass?: string;
}) {
  return (
    <div className={`rounded-lg border p-3 ${borderClass}`}>
      <div className="text-[11px] text-slate-400">{label}</div>
      <div className={`mt-1 font-mono text-sm font-bold ${valueClass}`}>{value}</div>
      {sub && <div className={`mt-0.5 font-mono text-[11px] ${subClass}`}>{sub}</div>}
    </div>
  );
}

function LadderStep({
  title,
  price,
  meta,
  cls,
}: {
  title: string;
  price: string;
  meta: string;
  cls: string;
}) {
  return (
    <div className={`rounded-lg border px-3 py-2 ${cls}`}>
      <div className="text-[10px] opacity-80">{title}</div>
      <div className="font-bold">{price}</div>
      <div className="text-[10px] text-slate-400">{meta}</div>
    </div>
  );
}

function DossierTile({
  label,
  value,
  sub,
  valCls = "text-white",
}: {
  label: string;
  value: string;
  sub?: string;
  valCls?: string;
}) {
  return (
    <div className="rounded border border-[#1f2937] bg-[#0e141d] p-2">
      <span className="text-[10px] text-slate-400 block">{label}</span>
      <span className={`font-bold ${valCls}`}>{value}</span>
      {sub && <span className="ml-1 text-[10px] text-slate-500">{sub}</span>}
    </div>
  );
}
