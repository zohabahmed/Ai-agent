"use client";

import React, { useEffect, useState } from "react";
import Link from "next/link";
import {
  ArrowRight,
  Bot,
  Download,
  Pin,
  RefreshCw,
  Search,
  Star,
} from "lucide-react";
import {
  Area,
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

interface GlobalSearchCoin {
  symbol: string;
  base: string;
  quote: string;
  exchange: string;
  market_type: string;
  last: number | null;
  change_24h_pct: number | null;
  volume_24h_usd: number;
}

const sign = (n: number) => (n >= 0 ? "+" : "");
const pnlCls = (n: number) => (n >= 0 ? "text-emerald-400" : "text-rose-400");

function countdown(openedAt: string, maxMin: number) {
  const left = maxMin * 60 - Math.floor((Date.now() - new Date(openedAt).getTime()) / 1000);
  if (left <= 0) return "exit due";
  const m = Math.floor(left / 60);
  const s = left % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

export default function ExchangeTerminalPage() {
  const {
    token,
    dash,
    active,
    scanner,
    pinnedCoins,
    scanLoading,
    busy,
    notify,
    refreshScanner,
    togglePinCoin,
    openTrade,
    bookProfit,
    closeAll,
  } = useTradingContext();

  const [symbol, setSymbol] = useState<string>(() => {
    const first = scanner?.coins?.find((c) => c.setup_status === "READY") || scanner?.coins?.[0];
    return first?.symbol || "SOL/USDT";
  });
  const [symbolManual, setSymbolManual] = useState(false);
  const [tf, setTf] = useState("15m");
  const [chartMode, setChartMode] = useState<"QUANT" | "TV">("QUANT");
  const { market } = useSymbolMarket(symbol, tf, 8000);
  const [searchQuery, setSearchQuery] = useState("");
  const [searchHits, setSearchHits] = useState<GlobalSearchCoin[]>([]);
  const [searchBusy, setSearchBusy] = useState(false);
  const [tab, setTab] = useState<"POSITIONS" | "HISTORY" | "LOG">("POSITIONS");
  const [, setTick] = useState(0);
  const [downloadingHistory, setDownloadingHistory] = useState(false);

  const handleDownloadHistory = async (format: "csv" | "json" = "csv") => {
    setDownloadingHistory(true);
    try {
      const filename = await TradingApi.downloadTradesHistory(token, format, "closed");
      notify(`Downloaded ${filename} successfully.`, true);
    } catch (err: any) {
      notify(err?.message || "Failed to download trade history", false);
    } finally {
      setDownloadingHistory(false);
    }
  };

  useEffect(() => {
    if (!symbolManual && scanner?.coins?.length) {
      const topReady = scanner.coins.find((c) => c.setup_status === "READY") || scanner.coins[0];
      if (topReady?.symbol) setSymbol(topReady.symbol);
    }
  }, [scanner, symbolManual]);

  useEffect(() => {
    const countdownTimer = setInterval(() => setTick((t) => t + 1), 1000);
    return () => clearInterval(countdownTimer);
  }, []);

  const handleGlobalSearch = async (rawQ: string) => {
    setSearchQuery(rawQ);
    const trimmed = rawQ.trim();
    if (!trimmed) {
      setSearchHits([]);
      return;
    }
    setSearchBusy(true);
    try {
      const res = (await TradingApi.searchGlobalMarkets(token, trimmed, 30)) as {
        markets: GlobalSearchCoin[];
      };
      setSearchHits(res.markets || []);
    } catch {
      setSearchHits([]);
    } finally {
      setSearchBusy(false);
    }
  };

  const selectAnyCoin = (rawInput: string) => {
    const clean = rawInput.trim().toUpperCase().replace("-", "/");
    if (!clean) return;
    const norm = clean.includes("/")
      ? clean
      : clean.endsWith("USDT") && clean.length > 4
      ? `${clean.slice(0, -4)}/USDT`
      : `${clean}/USDT`;
    setSymbol(norm);
    setSymbolManual(true);
    setSearchQuery("");
    setSearchHits([]);
    notify(`Loaded ${norm} chart & agent setup analyzer`);
  };

  const trades = active?.active_trades || [];
  const summ = active?.account_summary;
  const maxHold = summ?.max_hold_minutes ?? 240;
  const posOnSymbol = trades.find((t) => t.symbol === symbol);
  const coinRow = scanner?.coins.find((c) => c.symbol === symbol);
  const lastPx = market?.candles?.length
    ? market.candles[market.candles.length - 1].close
    : coinRow?.price ?? 0;
  const candles = (market?.candles || []).map((c) => ({
    ...c,
    t: new Date(c.timestamp).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
  }));

  return (
    <main className="mx-auto max-w-[1700px] text-[13px]">
      <div className="grid grid-cols-1 gap-px bg-[#1f2937] xl:grid-cols-[310px_1fr_310px]">
        {/* ===== LEFT: GLOBAL MARKET SCANNER & ANY-COIN SEARCH ===== */}
        <aside className="bg-[#0e141d] p-3">
          <div className="mb-2 flex items-center justify-between">
            <div>
              <div className="font-bold text-white">Global Scanner (Any Coin)</div>
              <div className="text-[11px] text-slate-400">
                {scanner ? `${scanner.scanned} live global · ${scanner.ready_count} setups ready` : "Discovering global markets…"}
              </div>
            </div>
            <button
              type="button"
              onClick={() => void refreshScanner(tf)}
              title="Refresh Global Market Scanner"
              className="rounded-lg border border-[#253142] bg-[#161f2c] p-1.5 text-slate-300 hover:text-white"
            >
              <RefreshCw className={`h-3.5 w-3.5 ${scanLoading ? "animate-spin" : ""}`} />
            </button>
          </div>

          <form
            onSubmit={(e) => {
              e.preventDefault();
              selectAnyCoin(searchQuery);
            }}
            className="mb-2 flex gap-1.5"
          >
            <div className="relative flex-1 min-w-0">
              <Search className="pointer-events-none absolute left-2 top-2 h-3.5 w-3.5 text-slate-500" />
              <input
                value={searchQuery}
                onChange={(e) => void handleGlobalSearch(e.target.value)}
                placeholder="Any coin (PEPE, WIF, TAO, KAS…)"
                className="w-full rounded-lg border border-[#253142] bg-[#080c12] py-1.5 pl-7 pr-2 text-xs text-white placeholder-slate-500 focus:border-amber-400 focus:outline-none"
              />
            </div>
            <button
              type="submit"
              disabled={!searchQuery.trim()}
              className="rounded-lg bg-amber-500/20 border border-amber-500/40 px-2.5 py-1 text-[11px] font-bold text-amber-300 hover:bg-amber-500/30 disabled:opacity-40"
            >
              Go
            </button>
          </form>

          {searchQuery.trim() && (
            <div className="mb-2 max-h-44 overflow-y-auto rounded-lg border border-[#253142] bg-[#080c12] p-1">
              {searchBusy ? (
                <div className="py-2 text-center text-[11px] text-slate-500">Searching global exchanges…</div>
              ) : searchHits.length > 0 ? (
                searchHits.map((m) => {
                  const isPinned = pinnedCoins.includes(m.symbol);
                  return (
                    <div key={`${m.exchange}-${m.symbol}`} className="flex items-center justify-between gap-1 rounded px-1.5 py-1 hover:bg-[#161f2c]">
                      <button type="button" onClick={() => selectAnyCoin(m.symbol)} className="flex-1 text-left">
                        <span className="font-semibold text-white">{m.symbol}</span>{" "}
                        <span className="text-[10px] text-slate-400">{m.exchange}</span>
                        {m.last !== null && <span className="ml-1 font-mono text-[10px] text-amber-300">${fmtUsd(m.last, pxDigits(m.last))}</span>}
                      </button>
                      <button
                        type="button"
                        onClick={() => void togglePinCoin(m.symbol)}
                        className={`rounded px-1.5 py-0.5 text-[10px] font-semibold ${
                          isPinned ? "bg-amber-500/20 text-amber-300" : "bg-[#1a2433] text-slate-300 hover:text-white"
                        }`}
                      >
                        {isPinned ? "Pinned" : "+Pin"}
                      </button>
                    </div>
                  );
                })
              ) : (
                <button
                  type="button"
                  onClick={() => selectAnyCoin(searchQuery)}
                  className="w-full py-1.5 text-center text-[11px] text-amber-300 hover:underline"
                >
                  Open {searchQuery.toUpperCase()} / USDT directly →
                </button>
              )}
            </div>
          )}

          <div className="grid grid-cols-[1fr_auto_auto] gap-x-2 border-b border-[#1f2937] pb-1 text-[10px] uppercase tracking-wider text-slate-500">
            <span>Pair</span>
            <span className="text-right">Price / 24h</span>
            <span className="text-right">Setup</span>
          </div>

          <div className="max-h-[460px] overflow-y-auto">
            {(scanner?.coins || []).map((c) => (
              <button
                key={c.symbol}
                type="button"
                onClick={() => {
                  setSymbol(c.symbol);
                  setSymbolManual(true);
                }}
                title={c.reason}
                className={`grid w-full grid-cols-[1fr_auto_auto] items-center gap-x-2 border-b border-[#161e2b] px-1.5 py-1.5 text-left transition hover:bg-[#161f2c] ${
                  symbol === c.symbol ? "bg-[#182231]" : ""
                }`}
              >
                <span>
                  <span className="font-semibold text-white">{c.symbol.replace("/USDT", "")}</span>
                  <span className="text-slate-500">/USDT</span>
                  {c.pinned && <Star className="ml-1 inline h-3 w-3 text-amber-400 fill-amber-400" />}
                  {trades.some((t) => t.symbol === c.symbol) && <span className="ml-1 text-emerald-400">●</span>}
                  <div className="text-[10px] text-slate-500">{c.regime.replace(/_/g, " ")}</div>
                </span>
                <span className="text-right font-mono">
                  <div className="text-white">{fmtUsd(c.price, pxDigits(c.price))}</div>
                  <div className={`text-[10px] ${pnlCls(c.change_pct)}`}>
                    {sign(c.change_pct)}
                    {c.change_pct.toFixed(2)}%
                  </div>
                </span>
                <span className="text-right">
                  <span
                    className={`rounded px-1.5 py-0.5 text-[10px] font-bold ${
                      c.setup_status === "READY"
                        ? c.direction === "SHORT"
                          ? "bg-rose-500/20 text-rose-300"
                          : "bg-emerald-500/20 text-emerald-300"
                        : c.setup_status === "FORMING"
                        ? "bg-amber-500/15 text-amber-300"
                        : "bg-slate-700/40 text-slate-400"
                    }`}
                  >
                    {c.setup_status === "WAIT" ? "WAIT" : c.direction}
                  </span>
                  <div className="text-[10px] font-mono text-slate-500">{c.score.toFixed(0)}</div>
                </span>
              </button>
            ))}
            {!scanner && <div className="py-8 text-center text-xs text-slate-500">Loading global futures pairs…</div>}
          </div>
        </aside>

        {/* ===== CENTER: LIVE PRICE & AGENT CHART ===== */}
        <section className="bg-[#0e141d] p-3 flex flex-col justify-between">
          <div>
            <div className="mb-2 flex flex-wrap items-center gap-3">
              <span className="text-lg font-bold text-white font-mono">{symbol.replace("/", "")} Perp</span>
              <span className="font-mono text-lg font-bold text-amber-300">
                {lastPx ? `$${fmtUsd(lastPx, pxDigits(lastPx))}` : "—"}
              </span>
              {coinRow && (
                <>
                  <span className={`font-mono text-xs font-semibold ${pnlCls(coinRow.change_pct)}`}>
                    {sign(coinRow.change_pct)}
                    {coinRow.change_pct.toFixed(2)}%
                  </span>
                  <span className="text-xs text-slate-400">
                    TV: <strong className="text-slate-200">{coinRow.tv_rating}</strong> · Regime:{" "}
                    <strong className="text-slate-200">{coinRow.regime}</strong>
                  </span>
                </>
              )}
              <div className="ml-auto flex items-center gap-1">
                {["5m", "15m", "1h", "4h"].map((x) => (
                  <button
                    key={x}
                    type="button"
                    onClick={() => setTf(x)}
                    className={`rounded-md px-2 py-0.5 font-mono text-xs transition ${
                      tf === x ? "bg-[#1e293b] text-amber-300 font-bold" : "text-slate-400 hover:text-white"
                    }`}
                  >
                    {x}
                  </button>
                ))}
                <span className="mx-1 h-4 w-px bg-[#1f2937]" />
                <button
                  type="button"
                  onClick={() => setChartMode("QUANT")}
                  className={`rounded-md px-2.5 py-0.5 text-xs transition ${
                    chartMode === "QUANT" ? "bg-[#1e293b] text-white font-semibold" : "text-slate-400 hover:text-white"
                  }`}
                >
                  Original
                </button>
                <button
                  type="button"
                  onClick={() => setChartMode("TV")}
                  className={`rounded-md px-2.5 py-0.5 text-xs transition ${
                    chartMode === "TV" ? "bg-[#1e293b] text-white font-semibold" : "text-slate-400 hover:text-white"
                  }`}
                >
                  TradingView
                </button>
              </div>
            </div>

            <div className="h-[460px] w-full rounded-lg border border-[#1f2937] bg-[#080c12]">
              {chartMode === "TV" ? (
                <iframe
                  title="tv"
                  className="h-full w-full rounded-lg border-0"
                  src={`https://s.tradingview.com/widgetembed/?symbol=${encodeURIComponent(
                    `BINANCE:${symbol.replace("/", "")}.P`
                  )}&interval=${{ "5m": "5", "15m": "15", "1h": "60", "4h": "240" }[tf]}&theme=dark&style=1&hidesidetoolbar=0`}
                />
              ) : candles.length ? (
                <ResponsiveContainer width="100%" height="100%">
                  <ComposedChart data={candles} margin={{ top: 10, right: 60, left: 0, bottom: 0 }}>
                    <defs>
                      <linearGradient id="g" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="5%" stopColor="#f59e0b" stopOpacity={0.25} />
                        <stop offset="95%" stopColor="#f59e0b" stopOpacity={0} />
                      </linearGradient>
                    </defs>
                    <CartesianGrid stroke="#16202e" />
                    <XAxis dataKey="t" stroke="#64748b" fontSize={10} minTickGap={30} />
                    <YAxis orientation="right" stroke="#64748b" fontSize={10} domain={["auto", "auto"]} />
                    <Tooltip contentStyle={{ background: "#0e141d", border: "1px solid #263244", fontSize: 12 }} />
                    <Area dataKey="close" stroke="#f59e0b" strokeWidth={2} fill="url(#g)" name="Price" />
                    <Line dataKey="ema_20" stroke="#10b981" dot={false} strokeWidth={1.3} name="EMA20" />
                    <Line dataKey="ema_50" stroke="#94a3b8" dot={false} strokeWidth={1.2} name="EMA50" />
                    {posOnSymbol && (
                      <ReferenceLine y={posOnSymbol.entry_price} stroke="#f59e0b" strokeDasharray="4 4" label={{ value: "ENTRY", fill: "#f59e0b", fontSize: 10, position: "left" }} />
                    )}
                    {posOnSymbol?.take_profit && (
                      <ReferenceLine y={posOnSymbol.take_profit} stroke="#10b981" strokeDasharray="4 4" label={{ value: "TP", fill: "#10b981", fontSize: 10, position: "left" }} />
                    )}
                    {posOnSymbol?.stop_loss && (
                      <ReferenceLine y={posOnSymbol.stop_loss} stroke="#f43f5e" strokeDasharray="4 4" label={{ value: "SL", fill: "#f43f5e", fontSize: 10, position: "left" }} />
                    )}
                  </ComposedChart>
                </ResponsiveContainer>
              ) : (
                <div className="flex h-full items-center justify-center text-xs text-slate-500">Loading chart…</div>
              )}
            </div>
          </div>

          {coinRow && (
            <div className="mt-2.5 flex flex-wrap items-center justify-between gap-2 rounded-lg border border-[#1f2937] bg-[#080c12] px-3 py-2 text-xs text-slate-400">
              <span>
                <strong className="text-slate-200">Agent view:</strong> {coinRow.reason}
                {coinRow.strategy && (
                  <>
                    {" "}
                    · strategy <strong className="text-amber-300">{coinRow.strategy}</strong>
                  </>
                )}
              </span>
              <Link href="/guide" className="text-[11px] font-semibold text-amber-300 hover:underline">
                How agent evaluates setups →
              </Link>
            </div>
          )}
        </section>

        {/* ===== RIGHT: EXECUTION & AGENT ORDER TICKET ===== */}
        <aside className="space-y-3 bg-[#0e141d] p-3">
          <div className="grid grid-cols-3 gap-1 text-xs">
            <span className="rounded-lg border border-[#253142] bg-[#161f2c] py-1.5 text-center font-semibold text-slate-200">
              Isolated
            </span>
            <span className="rounded-lg border border-amber-500/40 bg-amber-500/15 py-1.5 text-center font-semibold text-amber-300">
              1x / Auto
            </span>
            <span className="rounded-lg border border-[#253142] bg-[#161f2c] py-1.5 text-center font-semibold text-slate-200">
              Market
            </span>
          </div>

          <div className="rounded-lg border border-[#1f2937] bg-[#080c12] p-3 text-xs space-y-0.5">
            <Row k="Pair" v={symbol} />
            <Row k="Mark price" v={lastPx ? `$${fmtUsd(lastPx, pxDigits(lastPx))}` : "—"} />
            <Row k="Available" v={summ ? `$${fmtUsd(summ.available_usdt)} USDT` : "—"} />
            <Row k="Size" v="Auto (Fractional Kelly)" />
            <Row k="TP / SL" v="≥ 2.0R / ATR (0.8–2.5%)" />
            <Row k="Max hold" v={`${maxHold}m (60m MR)`} />
          </div>

          <div className="grid grid-cols-2 gap-2">
            <button
              type="button"
              onClick={() => void openTrade(symbol, "LONG", tf)}
              disabled={busy !== null}
              className="rounded-lg bg-emerald-500 py-2.5 text-xs font-bold text-slate-950 hover:bg-emerald-400 active:scale-[0.98] disabled:opacity-50"
            >
              Buy / Long
            </button>
            <button
              type="button"
              onClick={() => void openTrade(symbol, "SHORT", tf)}
              disabled={busy !== null}
              className="rounded-lg bg-rose-500 py-2.5 text-xs font-bold text-white hover:bg-rose-400 active:scale-[0.98] disabled:opacity-50"
            >
              Sell / Short
            </button>
          </div>

          <button
            type="button"
            onClick={() => void openTrade(symbol, undefined, tf)}
            disabled={busy !== null}
            className="flex w-full items-center justify-center gap-1.5 rounded-lg border border-amber-500/40 bg-amber-500/15 py-2 text-xs font-bold text-amber-200 hover:bg-amber-500/25 active:scale-[0.98] disabled:opacity-50"
          >
            <Bot className="h-3.5 w-3.5" />
            <span>Let agent decide direction on {symbol.replace("/USDT", "")}</span>
          </button>

          {posOnSymbol && (
            <div className="rounded-lg border border-amber-500/40 bg-[#080c12] p-3 text-xs">
              <div className="mb-1 flex justify-between">
                <span className={`font-bold ${posOnSymbol.side === "SHORT" ? "text-rose-400" : "text-emerald-400"}`}>
                  {posOnSymbol.side} {posOnSymbol.symbol}
                </span>
                <span className={`font-mono font-bold ${pnlCls(posOnSymbol.unrealized_pnl)}`}>
                  {sign(posOnSymbol.unrealized_pnl)}${fmtUsd(posOnSymbol.unrealized_pnl, 4)} ({sign(posOnSymbol.roi_pct)}
                  {posOnSymbol.roi_pct.toFixed(2)}%)
                </span>
              </div>
              <div className="mb-2 flex items-center justify-between text-[11px] text-slate-400">
                <span>Auto-close in {countdown(posOnSymbol.opened_at, maxHold)}</span>
                <Link href="/trade" className="text-amber-300 hover:underline inline-flex items-center gap-0.5">
                  Full Dossier <ArrowRight className="h-3 w-3" />
                </Link>
              </div>
              <BookButtons t={posOnSymbol} busy={busy} onBook={bookProfit} />
            </div>
          )}

          <div className="rounded-lg border border-[#1f2937] bg-[#080c12] p-3 text-[11px] leading-relaxed text-slate-400">
            <div className="mb-1.5 flex items-center justify-between font-semibold text-slate-200">
              <span>Agent rules</span>
              <button
                type="button"
                onClick={() => void togglePinCoin(symbol)}
                disabled={busy !== null}
                className={`inline-flex items-center gap-1 rounded px-2 py-0.5 text-[10px] font-semibold ${
                  pinnedCoins.includes(symbol) ? "bg-amber-500/20 text-amber-300" : "bg-[#161f2c] text-slate-300 hover:text-white"
                }`}
              >
                <Pin className="h-2.5 w-2.5" />
                <span>{pinnedCoins.includes(symbol) ? "Pinned" : "+ Pin Coin"}</span>
              </button>
            </div>
            <ul className="space-y-1">
              <li>• Futures only, Isolated margin (1x default; auto 2x–5x only when needed for small balances)</li>
              <li>• Real USDT live Futures from any positive wallet balance (Binance / MEXC / KuCoin / Gate)</li>
              <li>• Scans {scanner?.scanned ?? 64} global charts (15m + 1h HTF) &amp; any coin searched in the world</li>
              <li>• Books 50% at +1R to breakeven+fees; trails runner at 1.2x ATR (exchange SL/TP on Live)</li>
              <li>• Trend cap {maxHold}m / MR 60m · UTC 00:00 daily cap · Max 2 same-side trades</li>
            </ul>
          </div>
        </aside>
      </div>

      {/* ===== BOTTOM: POSITIONS / HISTORY / LOG ===== */}
      <section className="border-t border-[#1f2937] bg-[#0e141d]">
        <div className="flex items-center gap-4 border-b border-[#1f2937] px-4">
          {(
            [
              ["POSITIONS", `Open Positions (${trades.length})`],
              ["HISTORY", `Closed History (${active?.recently_closed.length ?? 0})`],
              ["LOG", "Agent Log"],
            ] as const
          ).map(([k, label]) => (
            <button
              key={k}
              type="button"
              onClick={() => setTab(k)}
              className={`border-b-2 py-2.5 text-xs font-semibold transition ${
                tab === k ? "border-amber-400 text-amber-300" : "border-transparent text-slate-400 hover:text-white"
              }`}
            >
              {label}
            </button>
          ))}
          {tab === "POSITIONS" && trades.length > 0 && (
            <div className="ml-auto flex items-center gap-2">
              <Link
                href="/trade"
                className="rounded-lg border border-[#2b3545] bg-[#161f2c] px-3 py-1 text-xs font-semibold text-slate-200 hover:text-white"
              >
                Open Trade Details →
              </Link>
              <button
                type="button"
                onClick={() => void closeAll()}
                disabled={busy !== null}
                className="rounded-lg bg-rose-600/85 px-3 py-1 text-xs font-bold text-white hover:bg-rose-500 disabled:opacity-50"
              >
                {busy === "close-all" ? "Closing…" : "Close All"}
              </button>
            </div>
          )}
          {tab === "HISTORY" && (
            <div className="ml-auto flex items-center gap-2 py-1">
              <button
                type="button"
                onClick={() => void handleDownloadHistory("csv")}
                disabled={downloadingHistory}
                className="inline-flex items-center gap-1.5 rounded-lg border border-amber-500/40 bg-amber-500/15 px-3 py-1 text-xs font-bold text-amber-300 hover:bg-amber-500/25 hover:text-amber-200 transition disabled:opacity-50"
                title="Download closed trade history with coin, entry/exit, profit/loss, and fees as CSV"
              >
                <Download className="h-3.5 w-3.5" />
                <span>{downloadingHistory ? "Downloading…" : "Download Trades (CSV)"}</span>
              </button>
              <button
                type="button"
                onClick={() => void handleDownloadHistory("json")}
                disabled={downloadingHistory}
                className="inline-flex items-center gap-1 rounded-lg border border-[#2b3545] bg-[#161f2c] px-2.5 py-1 text-xs font-semibold text-slate-300 hover:bg-[#1f2937] hover:text-white transition disabled:opacity-50"
                title="Download closed trade history as JSON"
              >
                <span>JSON</span>
              </button>
            </div>
          )}
        </div>

        <div className="overflow-x-auto px-4 pb-6">
          {tab === "POSITIONS" &&
            (trades.length ? (
              <table className="w-full min-w-[1100px] text-xs">
                <thead className="text-left text-[11px] uppercase tracking-wider text-slate-500">
                  <tr>
                    {["Symbol", "Size", "Entry", "Mark", "Liq. Price", "Margin", "PNL (ROI%)", "TP / SL", "Time left", "Book profit / Stop"].map((h) => (
                      <th key={h} className="py-2 font-medium">
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {trades.map((t) => (
                    <tr key={t.id} className="border-t border-[#161e2b] hover:bg-[#131b26]">
                      <td className="py-2">
                        <button
                          type="button"
                          onClick={() => {
                            setSymbol(t.symbol);
                            setSymbolManual(true);
                          }}
                          className="text-left"
                        >
                          <span className={`mr-1 rounded px-1 text-[10px] font-bold ${t.side === "SHORT" ? "bg-rose-500/20 text-rose-300" : "bg-emerald-500/20 text-emerald-300"}`}>
                            {t.side}
                          </span>
                          <strong className="text-white">{t.symbol.replace("/", "")}</strong>
                          <span className="ml-1 text-amber-300">{t.leverage || 1}x</span>
                          <div className="text-[10px] text-slate-500">{t.strategy_name}</div>
                        </button>
                      </td>
                      <td className="font-mono">
                        {t.quantity.toFixed(4)}
                        <div className="text-[10px] text-slate-500">${fmtUsd(t.current_notional_usd)}</div>
                      </td>
                      <td className="font-mono">${fmtUsd(t.entry_price, pxDigits(t.entry_price))}</td>
                      <td className="font-mono">${fmtUsd(t.mark_price, pxDigits(t.mark_price))}</td>
                      <td className="font-mono text-amber-300">{t.liquidation_price > 0 ? `$${fmtUsd(t.liquidation_price, pxDigits(t.entry_price))}` : "—"}</td>
                      <td className="font-mono">${fmtUsd(t.initial_margin_usd)}</td>
                      <td className={`font-mono font-bold ${pnlCls(t.unrealized_pnl)}`}>
                        {sign(t.unrealized_pnl)}${fmtUsd(t.unrealized_pnl, 4)}
                        <div className="text-[10px]">
                          ({sign(t.roi_pct)}
                          {t.roi_pct.toFixed(2)}%)
                        </div>
                      </td>
                      <td className="font-mono text-[11px]">
                        <span className="text-emerald-400">{t.take_profit ? `$${fmtUsd(t.take_profit, pxDigits(t.take_profit))}` : "—"}</span>
                        <br />
                        <span className="text-rose-400">{t.stop_loss ? `$${fmtUsd(t.stop_loss, pxDigits(t.stop_loss))}` : "—"}</span>
                      </td>
                      <td className="font-mono text-slate-300">{countdown(t.opened_at, maxHold)}</td>
                      <td>
                        <BookButtons t={t} busy={busy} onBook={bookProfit} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <div className="py-10 text-center text-xs text-slate-500">
                No open positions right now. Click <strong>Start Agent</strong>, click <strong>Scan &amp; Trade Now</strong>, or pick any coin from the scanner to open a Futures position.
              </div>
            ))}

          {tab === "HISTORY" &&
            ((active?.recently_closed || []).length ? (
              <div className="space-y-3">
                <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-slate-400">
                  <span>
                    Showing {active?.recently_closed.length} closed trade(s) with full profit/loss, entry/exit prices, fees, and execution details.
                  </span>
                  <div className="flex items-center gap-2">
                    <button
                      type="button"
                      onClick={() => void handleDownloadHistory("csv")}
                      disabled={downloadingHistory}
                      className="inline-flex items-center gap-1 font-semibold text-amber-400 hover:text-amber-300 underline disabled:opacity-50"
                    >
                      <Download className="h-3 w-3" />
                      <span>Export Trade Log (CSV)</span>
                    </button>
                    <span>·</span>
                    <button
                      type="button"
                      onClick={() => void handleDownloadHistory("json")}
                      disabled={downloadingHistory}
                      className="text-slate-400 hover:text-white underline disabled:opacity-50"
                    >
                      JSON
                    </button>
                  </div>
                </div>
                <table className="w-full min-w-[950px] text-xs">
                  <thead className="text-left text-[11px] uppercase tracking-wider text-slate-500">
                    <tr>
                      {["Symbol", "Side", "Leverage", "Strategy", "Qty", "Entry", "Exit", "Realized P&L", "ROI %", "Exit Reason", "Closed"].map((h) => (
                        <th key={h} className="py-2 font-medium">
                          {h}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {(active?.recently_closed || []).map((c) => {
                      const roi =
                        c.net_pnl_pct !== null && c.net_pnl_pct !== undefined
                          ? c.net_pnl_pct
                          : c.entry_price > 0 && c.quantity > 0
                          ? (c.realized_pnl / (c.entry_price * c.quantity)) * 100
                          : 0;
                      return (
                        <tr key={c.id} className="border-t border-[#161e2b] hover:bg-[#131b26]">
                          <td className="py-2 font-semibold text-white">{c.symbol}</td>
                          <td className={c.side === "SHORT" ? "text-rose-400 font-semibold" : "text-emerald-400 font-semibold"}>
                            <span className={`rounded px-1 text-[10px] ${c.side === "SHORT" ? "bg-rose-500/15" : "bg-emerald-500/15"}`}>
                              {c.side}
                            </span>
                          </td>
                          <td className="font-mono text-amber-300">{c.leverage ? `${c.leverage}x` : "1x"}</td>
                          <td className="text-slate-400 text-[11px]">{c.strategy_name || "—"}</td>
                          <td className="font-mono">{c.quantity.toFixed(4)}</td>
                          <td className="font-mono">${fmtUsd(c.entry_price, pxDigits(c.entry_price))}</td>
                          <td className="font-mono">${fmtUsd(c.exit_price, pxDigits(c.exit_price))}</td>
                          <td className={`font-mono font-bold ${pnlCls(c.realized_pnl)}`}>
                            {sign(c.realized_pnl)}${fmtUsd(c.realized_pnl, 4)}
                          </td>
                          <td className={`font-mono font-bold ${pnlCls(roi)}`}>
                            {sign(roi)}{roi.toFixed(2)}%
                          </td>
                          <td className="text-[11px] text-slate-400">{c.exit_reason || "CLOSED"}</td>
                          <td className="text-slate-400">
                            {c.closed_at
                              ? new Date(c.closed_at).toLocaleString([], {
                                  month: "short",
                                  day: "numeric",
                                  hour: "2-digit",
                                  minute: "2-digit",
                                })
                              : "—"}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            ) : (
              <div className="py-10 text-center text-xs text-slate-500 space-y-2">
                <div>No closed trades in history yet.</div>
                <div className="text-[11px] text-slate-600">
                  When trades exit, all details (coin, entry/exit prices, profit/loss, ROI %, fees, and hold time) will be displayed here and can be downloaded as a CSV file.
                </div>
              </div>
            ))}

          {tab === "LOG" && (
            <div className="max-h-72 space-y-1 overflow-y-auto py-2 font-mono text-[11px]">
              {(dash?.recent_events || []).map((e) => (
                <div key={e.id} className="flex gap-3">
                  <span className="text-slate-500">{new Date(e.created_at).toLocaleTimeString()}</span>
                  <span className={e.severity === "CRITICAL" ? "text-rose-400" : e.severity === "WARNING" ? "text-amber-300" : "text-emerald-400"}>
                    [{e.event_type}]
                  </span>
                  <span className="text-slate-300">{e.message}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      </section>
    </main>
  );
}

function Row({ k, v }: { k: string; v: string }) {
  return (
    <div className="flex justify-between py-0.5">
      <span className="text-slate-500">{k}</span>
      <span className="font-mono text-slate-200">{v}</span>
    </div>
  );
}

function BookButtons({
  t,
  busy,
  onBook,
}: {
  t: ActiveTradeDetailed;
  busy: string | null;
  onBook: (t: ActiveTradeDetailed, f: number) => Promise<void>;
}) {
  return (
    <div className="flex gap-1">
      {[0.25, 0.5].map((f) => (
        <button
          key={f}
          type="button"
          onClick={() => void onBook(t, f)}
          disabled={busy !== null}
          className="rounded-md bg-emerald-600/85 px-2 py-1 text-[11px] font-semibold text-white hover:bg-emerald-500 disabled:opacity-50"
        >
          Book {f * 100}%
        </button>
      ))}
      <button
        type="button"
        onClick={() => void onBook(t, 1)}
        disabled={busy !== null}
        className="rounded-md bg-rose-600/85 px-2 py-1 text-[11px] font-semibold text-white hover:bg-rose-500 disabled:opacity-50"
      >
        {busy === `close-${t.id}-1` ? "…" : "Close / Stop"}
      </button>
    </div>
  );
}
