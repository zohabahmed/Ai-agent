"use client";

import React, { useCallback, useEffect, useState } from "react";
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
import { TradingApi } from "@/services/api";
import type { MarketSymbolData, PortfolioData } from "@/types/trading";

interface CoinMarketChartProps {
  token: string;
  tickers: Record<string, Record<string, unknown>>;
  openPositions: PortfolioData["open_positions"];
}

const SYMBOLS = [
  "BTC/USDT",
  "ETH/USDT",
  "SOL/USDT",
  "BNB/USDT",
  "XRP/USDT",
  "DOGE/USDT",
  "ADA/USDT",
  "AVAX/USDT",
  "LINK/USDT",
  "DOT/USDT",
  "LTC/USDT",
  "NEAR/USDT",
  "APT/USDT",
  "ARB/USDT",
  "OP/USDT",
  "SUI/USDT",
];
const TIMEFRAMES = [
  { label: "5m", tvInterval: "5" },
  { label: "15m", tvInterval: "15" },
  { label: "1h", tvInterval: "60" },
  { label: "4h", tvInterval: "240" },
];

export default function CoinMarketChart({
  token,
  tickers,
  openPositions,
}: CoinMarketChartProps) {
  const activeTradedSymbol = openPositions?.[0]?.symbol || "BTC/USDT";
  const [selectedSymbol, setSelectedSymbol] =
    useState<string>(activeTradedSymbol);
  const [customCoinInput, setCustomCoinInput] = useState<string>("");
  const [timeframe, setTimeframe] = useState<string>("15m");
  const [chartMode, setChartMode] = useState<"QUANT" | "TRADINGVIEW">("QUANT");
  const [marketData, setMarketData] = useState<MarketSymbolData | null>(null);
  const [loading, setLoading] = useState<boolean>(true);

  const loadMarketSymbol = useCallback(async () => {
    if (!token) return;
    try {
      const res = (await TradingApi.getMarket(
        token,
        selectedSymbol,
        timeframe,
        90
      )) as MarketSymbolData;
      setMarketData(res);
    } catch {
      // Retain previous chart state on transient error
    } finally {
      setLoading(false);
    }
  }, [token, selectedSymbol, timeframe]);

  useEffect(() => {
    setLoading(true);
    void loadMarketSymbol();
    const timer = setInterval(() => {
      void loadMarketSymbol();
    }, 10000);
    return () => clearInterval(timer);
  }, [loadMarketSymbol]);

  const activePositionForSymbol = openPositions.find(
    (p) => p.symbol === selectedSymbol
  );

  const candles = (marketData?.candles || []).map((c) => ({
    ...c,
    timeLabel: new Date(c.timestamp).toLocaleTimeString([], {
      hour: "2-digit",
      minute: "2-digit",
    }),
  }));

  const tvRating = marketData?.tradingview_rating;
  const deriv = marketData?.derivatives_sentiment;
  const openbb = marketData?.openbb_quant;
  const regime = marketData?.regime;

  const tvInterval =
    TIMEFRAMES.find((t) => t.label === timeframe)?.tvInterval || "15";
  const tvCleanSymbol = `BINANCE:${selectedSymbol.replace("/", "")}`;

  const ratingBadgeClass = (rating?: string) => {
    if (!rating) return "border-slate-700 bg-slate-800 text-slate-300";
    if (rating.includes("BUY"))
      return "border-emerald-500/50 bg-emerald-500/20 text-emerald-300";
    if (rating.includes("SELL"))
      return "border-rose-500/50 bg-rose-500/20 text-rose-300";
    return "border-amber-500/50 bg-amber-500/15 text-amber-300";
  };

  return (
    <section className="rounded-xl border border-slate-800 bg-slate-900/80 p-5 shadow-lg">
      {/* Top Bar: Coin Selector, Timeframe Selector & Chart Engine Switcher */}
      <div className="flex flex-col justify-between gap-3 border-b border-slate-800 pb-4 lg:flex-row lg:items-center">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="text-base font-bold text-white">
              Live Coin Chart & Real-Time Derivatives / TradingView Intelligence
            </h2>
            {activePositionForSymbol && (
              <span className="rounded-full border border-emerald-500/50 bg-emerald-500/20 px-2.5 py-0.5 text-[11px] font-bold text-emerald-300">
                ACTIVE {activePositionForSymbol.side} POSITION ON{" "}
                {selectedSymbol}
              </span>
            )}
          </div>
          <p className="mt-0.5 text-xs text-slate-400">
            Real-time OHLCV + EMA 20/50 + Bollinger Bands + RSI (14) + CoinGlass / Binance Futures Positioning & TradingView 13-Indicator Technicals
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {/* Any Coin In The World Quick Load */}
          <form
            onSubmit={(e) => {
              e.preventDefault();
              const clean = customCoinInput.trim().toUpperCase().replace("-", "/");
              if (!clean) return;
              const norm = clean.includes("/") ? clean : `${clean}/USDT`;
              setSelectedSymbol(norm);
              setCustomCoinInput("");
            }}
            className="flex items-center gap-1"
          >
            <input
              value={customCoinInput}
              onChange={(e) => setCustomCoinInput(e.target.value)}
              placeholder="Any coin (PEPE, WIF…)"
              className="w-36 rounded-md border border-slate-700 bg-slate-950 px-2.5 py-1.5 text-xs text-white placeholder-slate-500 focus:border-indigo-500 focus:outline-none"
            />
            <button
              type="submit"
              className="rounded-md bg-indigo-600 px-2.5 py-1.5 text-xs font-bold text-white hover:bg-indigo-500"
            >
              Load
            </button>
          </form>
          {/* Coin Selector */}
          <div className="flex flex-wrap rounded-lg border border-slate-700 bg-slate-950 p-0.5">
            {SYMBOLS.map((sym) => {
              const hasOpenPos = openPositions.some((p) => p.symbol === sym);
              const tickerObj = tickers?.[sym] as
                | { last?: number }
                | undefined;
              const tickerPrice = tickerObj?.last;
              return (
                <button
                  key={sym}
                  type="button"
                  onClick={() => setSelectedSymbol(sym)}
                  className={`flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-bold transition ${
                    selectedSymbol === sym
                      ? "bg-indigo-600 text-white shadow"
                      : "text-slate-400 hover:text-white"
                  }`}
                >
                  <span>{sym}</span>
                  {typeof tickerPrice === "number" && (
                    <span className="font-mono text-[11px] opacity-90">
                      ${tickerPrice.toLocaleString()}
                    </span>
                  )}
                  {hasOpenPos && (
                    <span
                      className="h-2 w-2 rounded-full bg-emerald-400"
                      title="Currently trading this coin"
                    />
                  )}
                </button>
              );
            })}
          </div>

          {/* Timeframe Selector */}
          <div className="flex rounded-lg border border-slate-700 bg-slate-950 p-0.5">
            {TIMEFRAMES.map((tf) => (
              <button
                key={tf.label}
                type="button"
                onClick={() => setTimeframe(tf.label)}
                className={`rounded-md px-2.5 py-1.5 font-mono text-xs font-semibold transition ${
                  timeframe === tf.label
                    ? "bg-slate-800 text-indigo-300"
                    : "text-slate-400 hover:text-white"
                }`}
              >
                {tf.label}
              </button>
            ))}
          </div>

          {/* Chart Mode Toggle */}
          <div className="flex rounded-lg border border-slate-700 bg-slate-950 p-0.5">
            <button
              type="button"
              onClick={() => setChartMode("QUANT")}
              className={`rounded-md px-3 py-1.5 text-xs font-bold transition ${
                chartMode === "QUANT"
                  ? "bg-emerald-600 text-white"
                  : "text-slate-400 hover:text-white"
              }`}
            >
              Quant Indicators Chart
            </button>
            <button
              type="button"
              onClick={() => setChartMode("TRADINGVIEW")}
              className={`rounded-md px-3 py-1.5 text-xs font-bold transition ${
                chartMode === "TRADINGVIEW"
                  ? "bg-indigo-600 text-white"
                  : "text-slate-400 hover:text-white"
              }`}
            >
              TradingView Terminal
            </button>
          </div>
        </div>
      </div>

      {/* Main Content Grid: Left 8 Cols Chart, Right 4 Cols CoinGlass + TradingView + OpenBB Intelligence */}
      <div className="mt-4 grid grid-cols-1 gap-5 xl:grid-cols-12">
        {/* Left 8 Cols: Interactive Chart */}
        <div className="xl:col-span-8">
          {chartMode === "QUANT" ? (
            <div className="space-y-3">
              {/* Legend & Active Trade Overlay Banner */}
              <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-slate-800 bg-slate-950/70 px-3 py-2 text-xs">
                <div className="flex flex-wrap items-center gap-3">
                  <span className="font-bold text-white">
                    {selectedSymbol} ({timeframe})
                  </span>
                  <span className="flex items-center gap-1 text-indigo-300">
                    <span className="inline-block h-2 w-2 rounded-full bg-indigo-400" />
                    Close Price
                  </span>
                  <span className="flex items-center gap-1 text-emerald-300">
                    <span className="inline-block h-2 w-2 rounded-full bg-emerald-400" />
                    EMA 20
                  </span>
                  <span className="flex items-center gap-1 text-amber-300">
                    <span className="inline-block h-2 w-2 rounded-full bg-amber-400" />
                    EMA 50
                  </span>
                  <span className="flex items-center gap-1 text-cyan-300">
                    <span className="inline-block h-2 w-2 rounded-full bg-cyan-400" />
                    Bollinger (20, 2σ)
                  </span>
                </div>
                {activePositionForSymbol && (
                  <div className="flex flex-wrap items-center gap-2 font-mono text-[11px]">
                    <span className="text-indigo-300">
                      Entry: ${activePositionForSymbol.entry_price.toFixed(2)}
                    </span>
                    {activePositionForSymbol.stop_loss && (
                      <span className="text-rose-400">
                        SL: ${activePositionForSymbol.stop_loss.toFixed(2)}
                      </span>
                    )}
                    {activePositionForSymbol.take_profit && (
                      <span className="text-emerald-400">
                        TP: ${activePositionForSymbol.take_profit.toFixed(2)}
                      </span>
                    )}
                  </div>
                )}
              </div>

              {/* Price + EMA 20/50 + Bollinger Bands Chart */}
              <div className="h-72 w-full rounded-xl border border-slate-800 bg-slate-950/60 p-2">
                {candles.length > 0 ? (
                  <ResponsiveContainer width="100%" height="100%">
                    <ComposedChart data={candles}>
                      <defs>
                        <linearGradient
                          id="coinPriceGrad"
                          x1="0"
                          y1="0"
                          x2="0"
                          y2="1"
                        >
                          <stop
                            offset="5%"
                            stopColor="#6366f1"
                            stopOpacity={0.28}
                          />
                          <stop
                            offset="95%"
                            stopColor="#6366f1"
                            stopOpacity={0.0}
                          />
                        </linearGradient>
                      </defs>
                      <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                      <XAxis
                        dataKey="timeLabel"
                        stroke="#64748b"
                        fontSize={11}
                        minTickGap={24}
                      />
                      <YAxis
                        stroke="#64748b"
                        fontSize={11}
                        domain={["auto", "auto"]}
                        tickFormatter={(v) => `$${Number(v).toLocaleString()}`}
                      />
                      <Tooltip
                        contentStyle={{
                          backgroundColor: "#0f172a",
                          borderColor: "#334155",
                          fontSize: "12px",
                        }}
                      />
                      <Area
                        type="monotone"
                        dataKey="close"
                        stroke="#818cf8"
                        strokeWidth={2}
                        fill="url(#coinPriceGrad)"
                        name="Close Price"
                      />
                      <Line
                        type="monotone"
                        dataKey="ema_20"
                        stroke="#10b981"
                        strokeWidth={1.5}
                        dot={false}
                        name="EMA 20"
                      />
                      <Line
                        type="monotone"
                        dataKey="ema_50"
                        stroke="#f59e0b"
                        strokeWidth={1.5}
                        dot={false}
                        name="EMA 50"
                      />
                      <Line
                        type="monotone"
                        dataKey="bb_upper"
                        stroke="#22d3ee"
                        strokeDasharray="3 3"
                        strokeWidth={1}
                        dot={false}
                        name="BB Upper"
                      />
                      <Line
                        type="monotone"
                        dataKey="bb_lower"
                        stroke="#22d3ee"
                        strokeDasharray="3 3"
                        strokeWidth={1}
                        dot={false}
                        name="BB Lower"
                      />
                      {activePositionForSymbol && (
                        <>
                          <ReferenceLine
                            y={activePositionForSymbol.entry_price}
                            stroke="#818cf8"
                            strokeDasharray="4 4"
                            label={{
                              value: "ENTRY",
                              fill: "#818cf8",
                              fontSize: 10,
                            }}
                          />
                          {activePositionForSymbol.stop_loss && (
                            <ReferenceLine
                              y={activePositionForSymbol.stop_loss}
                              stroke="#f43f5e"
                              strokeDasharray="4 4"
                              label={{
                                value: "STOP LOSS",
                                fill: "#f43f5e",
                                fontSize: 10,
                              }}
                            />
                          )}
                          {activePositionForSymbol.take_profit && (
                            <ReferenceLine
                              y={activePositionForSymbol.take_profit}
                              stroke="#10b981"
                              strokeDasharray="4 4"
                              label={{
                                value: "TAKE PROFIT",
                                fill: "#10b981",
                                fontSize: 10,
                              }}
                            />
                          )}
                        </>
                      )}
                    </ComposedChart>
                  </ResponsiveContainer>
                ) : (
                  <div className="flex h-full items-center justify-center text-xs text-slate-500">
                    {loading
                      ? "Loading live market candles..."
                      : "No candles available for this symbol."}
                  </div>
                )}
              </div>

              {/* Sub-Chart: RSI (14) Oscillator & Volume */}
              <div className="h-36 w-full rounded-xl border border-slate-800 bg-slate-950/60 p-2">
                <div className="mb-1 flex items-center justify-between px-2 text-[11px] text-slate-400">
                  <span>RSI (14) Momentum & Volume Profile</span>
                  <span className="font-mono">
                    Latest RSI:{" "}
                    <strong className="text-indigo-300">
                      {candles.length > 0
                        ? candles[candles.length - 1].rsi_14?.toFixed(1)
                        : "--"}
                    </strong>{" "}
                    (Overbought 70 / Oversold 30)
                  </span>
                </div>
                {candles.length > 0 && (
                  <ResponsiveContainer width="100%" height="82%">
                    <ComposedChart data={candles}>
                      <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                      <XAxis
                        dataKey="timeLabel"
                        stroke="#64748b"
                        fontSize={10}
                        hide
                      />
                      <YAxis
                        yAxisId="rsi"
                        stroke="#64748b"
                        fontSize={10}
                        domain={[0, 100]}
                        ticks={[30, 50, 70]}
                      />
                      <YAxis
                        yAxisId="vol"
                        orientation="right"
                        stroke="#475569"
                        fontSize={10}
                        hide
                      />
                      <Tooltip
                        contentStyle={{
                          backgroundColor: "#0f172a",
                          borderColor: "#334155",
                          fontSize: "11px",
                        }}
                      />
                      <Bar
                        yAxisId="vol"
                        dataKey="volume"
                        fill="#334155"
                        opacity={0.45}
                        name="Volume"
                      />
                      <ReferenceLine
                        yAxisId="rsi"
                        y={70}
                        stroke="#f43f5e"
                        strokeDasharray="3 3"
                      />
                      <ReferenceLine
                        yAxisId="rsi"
                        y={30}
                        stroke="#10b981"
                        strokeDasharray="3 3"
                      />
                      <Line
                        yAxisId="rsi"
                        type="monotone"
                        dataKey="rsi_14"
                        stroke="#a855f7"
                        strokeWidth={1.8}
                        dot={false}
                        name="RSI (14)"
                      />
                    </ComposedChart>
                  </ResponsiveContainer>
                )}
              </div>
            </div>
          ) : (
            /* Live TradingView Advanced Chart Widget Embed */
            <div className="h-[440px] w-full overflow-hidden rounded-xl border border-slate-800 bg-slate-950">
              <iframe
                title={`TradingView ${selectedSymbol}`}
                src={`https://s.tradingview.com/widgetembed/?symbol=${encodeURIComponent(
                  tvCleanSymbol
                )}&interval=${tvInterval}&theme=dark&style=1&timezone=Etc%2FUTC&withdateranges=1&hide_side_toolbar=0&allow_symbol_change=1&studies=RSI%40tv-basicstudies%1FMACD%40tv-basicstudies%1FBB%40tv-basicstudies`}
                className="h-full w-full border-0"
                allowFullScreen
              />
            </div>
          )}
        </div>

        {/* Right 4 Cols: TradingView Technical Gauge + CoinGlass / Binance Futures Derivatives Sentiment */}
        <div className="space-y-3 xl:col-span-4">
          {/* Card 1: TradingView 13-Indicator Technical Rating */}
          <div className="rounded-xl border border-slate-800 bg-slate-950/85 p-3.5">
            <div className="flex items-center justify-between">
              <span className="text-xs font-bold uppercase tracking-wider text-indigo-300">
                TradingView Technical Rating
              </span>
              <span
                className={`rounded-md border px-2 py-0.5 text-xs font-bold ${ratingBadgeClass(
                  tvRating?.overall_rating
                )}`}
              >
                {tvRating?.overall_rating || "NEUTRAL"} (
                {tvRating ? (tvRating.rating_score >= 0 ? "+" : "") : ""}
                {tvRating?.rating_score?.toFixed(2) || "0.00"})
              </span>
            </div>

            <div className="mt-2.5 grid grid-cols-2 gap-2 text-xs">
              <div className="rounded-lg border border-slate-800 bg-slate-900/90 p-2">
                <div className="flex items-center justify-between">
                  <span className="text-[11px] text-slate-400">Moving Avgs</span>
                  <span className="font-bold text-white">
                    {tvRating?.ma_rating || "NEUTRAL"}
                  </span>
                </div>
                <div className="mt-1 flex justify-between font-mono text-[10px]">
                  <span className="text-emerald-400">
                    Buy: {tvRating?.ma_buy_count ?? 0}
                  </span>
                  <span className="text-slate-400">
                    Neut: {tvRating?.ma_neutral_count ?? 0}
                  </span>
                  <span className="text-rose-400">
                    Sell: {tvRating?.ma_sell_count ?? 0}
                  </span>
                </div>
              </div>

              <div className="rounded-lg border border-slate-800 bg-slate-900/90 p-2">
                <div className="flex items-center justify-between">
                  <span className="text-[11px] text-slate-400">Oscillators</span>
                  <span className="font-bold text-white">
                    {tvRating?.oscillator_rating || "NEUTRAL"}
                  </span>
                </div>
                <div className="mt-1 flex justify-between font-mono text-[10px]">
                  <span className="text-emerald-400">
                    Buy: {tvRating?.osc_buy_count ?? 0}
                  </span>
                  <span className="text-slate-400">
                    Neut: {tvRating?.osc_neutral_count ?? 0}
                  </span>
                  <span className="text-rose-400">
                    Sell: {tvRating?.osc_sell_count ?? 0}
                  </span>
                </div>
              </div>
            </div>

            {tvRating?.signals && (
              <div className="mt-2.5 flex flex-wrap gap-1">
                {Object.entries(tvRating.signals)
                  .slice(0, 8)
                  .map(([ind, sig]) => (
                    <span
                      key={ind}
                      className={`rounded border px-1.5 py-0.5 font-mono text-[10px] ${
                        sig === "BUY"
                          ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-300"
                          : sig === "SELL"
                          ? "border-rose-500/30 bg-rose-500/10 text-rose-300"
                          : "border-slate-800 bg-slate-900 text-slate-400"
                      }`}
                    >
                      {ind}: {sig}
                    </span>
                  ))}
              </div>
            )}
          </div>

          {/* Card 2: CoinGlass / Binance USD-M Futures Real-Time Derivatives Positioning */}
          <div className="rounded-xl border border-slate-800 bg-slate-950/85 p-3.5">
            <div className="flex items-center justify-between">
              <span className="text-xs font-bold uppercase tracking-wider text-amber-300">
                CoinGlass / Futures Orderflow
              </span>
              <span className="rounded border border-slate-700 bg-slate-900 px-2 py-0.5 font-mono text-[10px] text-slate-300">
                {deriv?.crowding_state || "BALANCED"}
              </span>
            </div>

            <div className="mt-2.5 grid grid-cols-2 gap-2 text-xs">
              <div className="rounded-lg border border-slate-800 bg-slate-900/90 p-2">
                <div className="text-[10px] text-slate-400">8h Funding Rate</div>
                <div
                  className={`mt-0.5 font-mono text-sm font-bold ${
                    (deriv?.funding_rate_pct || 0) > 0.03
                      ? "text-amber-300"
                      : (deriv?.funding_rate_pct || 0) < 0
                      ? "text-cyan-300"
                      : "text-emerald-400"
                  }`}
                >
                  {(deriv?.funding_rate_pct ?? 0) >= 0 ? "+" : ""}
                  {deriv?.funding_rate_pct?.toFixed(4) ?? "0.0100"}%
                </div>
                <div className="text-[10px] text-slate-500">
                  APR: {deriv?.annualized_funding_pct?.toFixed(1) ?? "10.9"}%
                </div>
              </div>

              <div className="rounded-lg border border-slate-800 bg-slate-900/90 p-2">
                <div className="text-[10px] text-slate-400">Open Interest</div>
                <div className="mt-0.5 font-mono text-sm font-bold text-white">
                  {deriv?.open_interest_usd
                    ? `$${(deriv.open_interest_usd / 1e9).toFixed(2)}B`
                    : `${deriv?.open_interest_coins?.toLocaleString() || "--"}`}
                </div>
                <div className="text-[10px] text-slate-500">
                  Basis: {(deriv?.basis_pct ?? 0) >= 0 ? "+" : ""}
                  {deriv?.basis_pct?.toFixed(3) ?? "0.000"}%
                </div>
              </div>
            </div>

            {/* Long vs Short Account Ratio Visual Bar */}
            <div className="mt-2.5">
              <div className="flex justify-between text-[11px]">
                <span className="font-semibold text-emerald-400">
                  Longs: {deriv?.long_account_pct?.toFixed(1) ?? "50.0"}%
                </span>
                <span className="font-mono text-slate-300">
                  L/S Ratio: {deriv?.long_short_ratio?.toFixed(2) ?? "1.00"}
                </span>
                <span className="font-semibold text-rose-400">
                  Shorts: {deriv?.short_account_pct?.toFixed(1) ?? "50.0"}%
                </span>
              </div>
              <div className="mt-1 flex h-2 w-full overflow-hidden rounded-full bg-rose-500/40">
                <div
                  className="bg-emerald-500 transition-all"
                  style={{
                    width: `${Math.min(
                      95,
                      Math.max(5, deriv?.long_account_pct ?? 50)
                    )}%`,
                  }}
                />
              </div>
            </div>

            {/* Taker Buy/Sell Volume Ratio & Survival Gate */}
            <div className="mt-2.5 flex items-center justify-between border-t border-slate-800/80 pt-2 text-[11px]">
              <span className="text-slate-400">
                Taker Buy/Sell Vol:{" "}
                <strong
                  className={
                    (deriv?.taker_buy_sell_ratio ?? 1) >= 1
                      ? "text-emerald-400"
                      : "text-rose-400"
                  }
                >
                  {deriv?.taker_buy_sell_ratio?.toFixed(2) ?? "1.00"}x
                </strong>
              </span>
              <div className="flex gap-1.5 font-mono text-[10px]">
                <span
                  className={
                    deriv?.allow_long !== false
                      ? "text-emerald-400"
                      : "text-rose-400"
                  }
                >
                  LONG:{deriv?.allow_long !== false ? "OK" : "BLOCKED"}
                </span>
                <span
                  className={
                    deriv?.allow_short !== false
                      ? "text-emerald-400"
                      : "text-rose-400"
                  }
                >
                  SHORT:{deriv?.allow_short !== false ? "OK" : "BLOCKED"}
                </span>
              </div>
            </div>
            {deriv?.rationale && (
              <p className="mt-1.5 text-[11px] text-slate-400">
                {deriv.rationale}
              </p>
            )}
          </div>

          {/* Card 3: OpenBB Quantitative Regime & Kelly Confluence */}
          <div className="rounded-xl border border-slate-800 bg-slate-950/85 p-3.5">
            <div className="flex items-center justify-between">
              <span className="text-xs font-bold uppercase tracking-wider text-emerald-300">
                OpenBB ODP Quant Confluence
              </span>
              <span className="font-mono text-xs font-bold text-white">
                {regime?.regime || "RANGING"} (ADX{" "}
                {regime?.adx?.toFixed(1) || "0.0"})
              </span>
            </div>
            <div className="mt-2 grid grid-cols-3 gap-2 font-mono text-[11px]">
              <div className="rounded border border-slate-800 bg-slate-900 p-1.5">
                <div className="text-[10px] text-slate-400">Hurst Exp</div>
                <div className="font-bold text-indigo-300">
                  {openbb?.hurst_exponent?.toFixed(3) ?? "0.500"}
                </div>
              </div>
              <div className="rounded border border-slate-800 bg-slate-900 p-1.5">
                <div className="text-[10px] text-slate-400">Sharpe / Sortino</div>
                <div className="font-bold text-emerald-300">
                  {openbb?.rolling_sharpe?.toFixed(2) ?? "0.00"} /{" "}
                  {openbb?.sortino_ratio?.toFixed(2) ?? "0.00"}
                </div>
              </div>
              <div className="rounded border border-slate-800 bg-slate-900 p-1.5">
                <div className="text-[10px] text-slate-400">Kelly Fraction</div>
                <div className="font-bold text-amber-300">
                  {((openbb?.kelly_optimal_fraction ?? 0) * 100).toFixed(1)}%
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
