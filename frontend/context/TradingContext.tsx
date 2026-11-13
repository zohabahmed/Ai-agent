"use client";

import React, { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { TradingApi } from "@/services/api";
import type {
  ActiveTradeDetailed,
  ActiveTradesResponse,
  ApprovalsData,
  DashboardData,
  ExchangeStatusData,
  MarketSymbolData,
  PortfolioData,
  StrategiesData,
  TradesData,
  WithdrawalsData,
} from "@/types/trading";

export const DEFAULT_TOKEN = "direct-operator";

export interface ScannerCoin {
  symbol: string;
  price: number;
  change_pct: number;
  volume_24h_usd?: number;
  pinned?: boolean;
  regime: string;
  tv_rating: string;
  tv_score: number;
  setup_status: "READY" | "FORMING" | "WAIT" | string;
  direction: string;
  strategy: string | null;
  score: number;
  reason: string;
}

export interface ScannerData {
  scanned: number;
  ready_count: number;
  scope?: string;
  pinned?: string[];
  coins: ScannerCoin[];
}

export interface CycleRunResult {
  orders_submitted?: number;
  scanned_symbols?: number;
  executed_trades?: Array<{
    symbol: string;
    side: string;
    fill_price: number;
    leverage: number;
  }>;
  status_reason?: string;
}

interface TradingContextValue {
  token: string;
  dash: DashboardData | null;
  active: ActiveTradesResponse | null;
  portfolio: PortfolioData | null;
  exchangeStatus: ExchangeStatusData | null;
  trades: TradesData | null;
  strategies: StrategiesData | null;
  approvals: ApprovalsData | null;
  withdrawals: WithdrawalsData | null;
  scanner: ScannerData | null;
  pinnedCoins: string[];
  scanLoading: boolean;
  loading: boolean;
  busy: string | null;
  error: string | null;
  toast: { msg: string; ok: boolean } | null;
  notify: (msg: string, ok?: boolean) => void;
  refreshCore: () => Promise<void>;
  refreshAll: () => Promise<void>;
  refreshScanner: (timeframe?: string) => Promise<void>;
  syncBalance: () => Promise<void>;
  toggleAgent: () => Promise<void>;
  runCycle: (timeframe?: string) => Promise<CycleRunResult | null>;
  emergency: () => Promise<void>;
  togglePinCoin: (sym: string) => Promise<void>;
  openTrade: (sym: string, side?: "LONG" | "SHORT", timeframe?: string) => Promise<any>;
  bookProfit: (t: ActiveTradeDetailed, fraction: number) => Promise<void>;
  closeAll: () => Promise<void>;
  handleApprovalDecision: (approvalId: string, approve: boolean) => Promise<void>;
}

const TradingContext = createContext<TradingContextValue | null>(null);

export const fmtUsd = (n: number, d = 2) =>
  n.toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d });

export const pxDigits = (p: number) => (p >= 100 ? 2 : p >= 1 ? 4 : 6);

export function TradingProvider({ children }: { children: React.ReactNode }) {
  const token = DEFAULT_TOKEN;
  const [dash, setDash] = useState<DashboardData | null>(null);
  const [active, setActive] = useState<ActiveTradesResponse | null>(null);
  const [portfolio, setPortfolio] = useState<PortfolioData | null>(null);
  const [exchangeStatus, setExchangeStatus] = useState<ExchangeStatusData | null>(null);
  const [trades, setTrades] = useState<TradesData | null>(null);
  const [strategies, setStrategies] = useState<StrategiesData | null>(null);
  const [approvals, setApprovals] = useState<ApprovalsData | null>(null);
  const [withdrawals, setWithdrawals] = useState<WithdrawalsData | null>(null);
  const [scanner, setScanner] = useState<ScannerData | null>(null);
  const [pinnedCoins, setPinnedCoins] = useState<string[]>([]);
  const [scanLoading, setScanLoading] = useState(false);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<{ msg: string; ok: boolean } | null>(null);
  const toastTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const notify = useCallback((msg: string, ok = true) => {
    setToast({ msg, ok });
    if (toastTimerRef.current) clearTimeout(toastTimerRef.current);
    toastTimerRef.current = setTimeout(() => setToast(null), 5500);
  }, []);

  const refreshCore = useCallback(async () => {
    try {
      const [d, a] = await Promise.all([
        TradingApi.getDashboard(token),
        TradingApi.getActiveTrades(token).catch(() => null),
      ]);
      setDash(d as DashboardData);
      if (a) setActive(a as ActiveTradesResponse);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to reach trading backend");
    } finally {
      setLoading(false);
    }
  }, [token]);

  const refreshAnalytics = useCallback(async () => {
    try {
      const [p, ex, t, s, ap, w] = await Promise.all([
        TradingApi.getPortfolio(token).catch(() => null),
        TradingApi.getExchangeStatus(token).catch(() => null),
        TradingApi.getTrades(token).catch(() => null),
        TradingApi.getStrategies(token).catch(() => null),
        TradingApi.getApprovals(token).catch(() => null),
        TradingApi.getWithdrawals(token).catch(() => null),
      ]);
      if (p) setPortfolio(p as PortfolioData);
      if (ex) setExchangeStatus(ex as ExchangeStatusData);
      if (t) setTrades(t as TradesData);
      if (s) setStrategies(s as StrategiesData);
      if (ap) setApprovals(ap as ApprovalsData);
      if (w) setWithdrawals(w as WithdrawalsData);
    } catch {
      // Keep prior snapshots on transient failures
    }
  }, [token]);

  const refreshAll = useCallback(async () => {
    await Promise.all([refreshCore(), refreshAnalytics()]);
  }, [refreshCore, refreshAnalytics]);

  const refreshScanner = useCallback(
    async (timeframe = "15m") => {
      setScanLoading(true);
      try {
        const sc = (await TradingApi.getScanner(token, timeframe, "GLOBAL")) as ScannerData;
        setScanner(sc);
        if (Array.isArray(sc.pinned)) setPinnedCoins(sc.pinned);
      } catch {
        // Retain last scanner result
      } finally {
        setScanLoading(false);
      }
    },
    [token]
  );

  useEffect(() => {
    void refreshAll();
    void refreshScanner("15m");
    const coreTimer = setInterval(() => void refreshCore(), 5000);
    const analyticsTimer = setInterval(() => void refreshAnalytics(), 15000);
    const scanTimer = setInterval(() => void refreshScanner("15m"), 60000);
    return () => {
      clearInterval(coreTimer);
      clearInterval(analyticsTimer);
      clearInterval(scanTimer);
    };
  }, [refreshAll, refreshCore, refreshAnalytics, refreshScanner]);

  const runAction = useCallback(
    async <T,>(key: string, fn: () => Promise<T>, okMsg: (r: T) => string): Promise<T | null> => {
      setBusy(key);
      try {
        const r = await fn();
        notify(okMsg(r), true);
        await refreshAll();
        return r;
      } catch (e) {
        notify(e instanceof Error ? e.message : "Action failed", false);
        return null;
      } finally {
        setBusy(null);
      }
    },
    [notify, refreshAll]
  );

  const syncBalance = useCallback(async () => {
    await runAction(
      "sync-bal",
      () =>
        TradingApi.syncExchangeBalances(token) as Promise<{
          available_quote?: number;
          exchange_id?: string;
        }>,
      (r) =>
        `Synced ${(r?.exchange_id || "exchange").toUpperCase()} Futures balance: $${(
          r?.available_quote ?? 0
        ).toFixed(2)} USDT`
    );
  }, [runAction, token]);

  const toggleAgent = useCallback(async () => {
    const isRunning = Boolean(dash?.account.is_trading_active);
    await runAction(
      "agent",
      () => (isRunning ? TradingApi.stopTrading(token) : TradingApi.startTrading(token)) as Promise<{ warning?: string }>,
      (r) =>
        isRunning
          ? "Autonomous trading loop paused"
          : r?.warning
          ? `Agent started · ${r.warning}`
          : "Autonomous agent started — scanning all markets"
    );
  }, [dash?.account.is_trading_active, runAction, token]);

  const runCycle = useCallback(
    async (timeframe = "15m"): Promise<CycleRunResult | null> => {
      return await runAction(
        "cycle",
        () => TradingApi.runCycleNow(token, timeframe) as Promise<CycleRunResult>,
        (r) => {
          if ((r?.orders_submitted ?? 0) > 0 && r?.executed_trades?.length) {
            const t = r.executed_trades[0];
            return `Opened ${t.side} ${t.symbol} (${t.leverage}x Isolated) @ $${fmtUsd(
              t.fill_price,
              pxDigits(t.fill_price)
            )}`;
          }
          return (
            r?.status_reason ||
            `Scanned ${r?.scanned_symbols ?? 0} charts · ${r?.orders_submitted ?? 0} new order(s)`
          );
        }
      );
    },
    [runAction, token]
  );

  const emergency = useCallback(async () => {
    const halted = Boolean(dash?.account.emergency_stopped || dash?.survival.emergency_stopped);
    await runAction(
      "halt",
      () =>
        halted
          ? TradingApi.resetRecovery(token)
          : TradingApi.emergencyStop(token, "Operator emergency stop triggered from UI header"),
      () => (halted ? "Emergency stop cleared into RECOVERY mode" : "EMERGENCY STOP ACTIVATED — trading halted")
    );
  }, [dash?.account.emergency_stopped, dash?.survival.emergency_stopped, runAction, token]);

  const togglePinCoin = useCallback(
    async (sym: string) => {
      const isPinned = pinnedCoins.includes(sym);
      await runAction(
        `pin-${sym}`,
        () => TradingApi.updateWatchlist(token, sym, isPinned ? "remove" : "add") as Promise<{ symbols?: string[] }>,
        (r) => {
          if (Array.isArray(r?.symbols)) setPinnedCoins(r.symbols);
          return isPinned ? `Removed ${sym} from watchlist` : `Pinned ${sym} for continuous global scanning`;
        }
      );
      void refreshScanner("15m");
    },
    [pinnedCoins, refreshScanner, runAction, token]
  );

  const openTrade = useCallback(
    async (sym: string, side?: "LONG" | "SHORT", timeframe = "15m") => {
      return await runAction(
        `open-${sym}-${side || "AUTO"}`,
        () => TradingApi.openTrade(token, sym, side, timeframe) as Promise<any>,
        (r) =>
          r?.trade?.executed
            ? `Opened ${r.trade.side} ${sym} (${r.trade.leverage || 1}x Isolated) @ $${fmtUsd(
                r.trade.fill_price,
                pxDigits(r.trade.fill_price)
              )}`
            : `Not opened on ${sym}: ${
                (r?.risk_and_kelly?.rejection_reasons || [r?.trade?.reason]).filter(Boolean).join(", ") ||
                "Risk gate check"
              }`
      );
    },
    [runAction, token]
  );

  const bookProfit = useCallback(
    async (t: ActiveTradeDetailed, fraction: number) => {
      await runAction(
        `close-${t.id}-${fraction}`,
        () =>
          TradingApi.closePosition(token, t.id, fraction) as Promise<{
            exit_price: number;
            realized_pnl: number;
          }>,
        (r) =>
          `${fraction < 1 ? `Booked ${Math.round(fraction * 100)}%` : "Closed"} ${t.symbol} @ $${fmtUsd(
            r.exit_price,
            pxDigits(r.exit_price)
          )} · P&L ${r.realized_pnl >= 0 ? "+" : ""}$${r.realized_pnl.toFixed(4)}`
      );
    },
    [runAction, token]
  );

  const closeAll = useCallback(async () => {
    await runAction(
      "close-all",
      () =>
        TradingApi.closeAllPositions(token) as Promise<{
          closed: number;
          total_realized_pnl: number;
        }>,
      (r) =>
        `Closed ${r.closed} position(s) · P&L ${
          r.total_realized_pnl >= 0 ? "+" : ""
        }$${r.total_realized_pnl.toFixed(4)}`
    );
  }, [runAction, token]);

  const handleApprovalDecision = useCallback(
    async (approvalId: string, approve: boolean) => {
      await runAction(
        `app-${approvalId}`,
        () =>
          approve
            ? TradingApi.approveOperation(token, approvalId)
            : TradingApi.rejectOperation(token, approvalId),
        () => (approve ? "Approval granted" : "Operation rejected")
      );
    },
    [runAction, token]
  );

  return (
    <TradingContext.Provider
      value={{
        token,
        dash,
        active,
        portfolio,
        exchangeStatus,
        trades,
        strategies,
        approvals,
        withdrawals,
        scanner,
        pinnedCoins,
        scanLoading,
        loading,
        busy,
        error,
        toast,
        notify,
        refreshCore,
        refreshAll,
        refreshScanner,
        syncBalance,
        toggleAgent,
        runCycle,
        emergency,
        togglePinCoin,
        openTrade,
        bookProfit,
        closeAll,
        handleApprovalDecision,
      }}
    >
      {children}
    </TradingContext.Provider>
  );
}

export function useTradingContext() {
  const ctx = useContext(TradingContext);
  if (!ctx) {
    throw new Error("useTradingContext must be used inside TradingProvider");
  }
  return ctx;
}

/**
 * Poll chart and indicator data for one symbol/timeframe while guarding against stale out-of-order responses.
 */
export function useSymbolMarket(symbol: string, timeframe: string, pollMs = 8000) {
  const { token } = useTradingContext();
  const [rawMarket, setRawMarket] = useState<MarketSymbolData | null>(null);
  const reqIdRef = useRef(0);

  const reload = useCallback(async () => {
    const reqId = ++reqIdRef.current;
    try {
      const mkt = (await TradingApi.getMarket(token, symbol, timeframe, 96)) as MarketSymbolData;
      if (reqId === reqIdRef.current) {
        setRawMarket(mkt);
      }
    } catch {
      // Keep matching prior candles on transient network errors; never leak another symbol
    }
  }, [token, symbol, timeframe]);

  useEffect(() => {
    setRawMarket((prev) => (prev && prev.symbol === symbol ? prev : null));
    void reload();
    const timer = setInterval(() => void reload(), pollMs);
    return () => {
      reqIdRef.current += 1;
      clearInterval(timer);
    };
  }, [reload, symbol, pollMs]);

  const market = rawMarket && rawMarket.symbol === symbol ? rawMarket : null;
  return { market, reload };
}
