"use client";

import { useCallback, useEffect, useState } from "react";
import { TradingApi } from "@/services/api";
import type {
  ApprovalsData,
  DashboardData,
  ExchangeStatusData,
  PortfolioData,
  StrategiesData,
  TradesData,
  WithdrawalsData,
} from "@/types/trading";

export function useTradingData(token: string | null = "direct-operator") {
  const effectiveToken = token || "direct-operator";
  const [dashboard, setDashboard] = useState<DashboardData | null>(null);
  const [portfolio, setPortfolio] = useState<PortfolioData | null>(null);
  const [exchangeStatus, setExchangeStatus] = useState<ExchangeStatusData | null>(null);
  const [trades, setTrades] = useState<TradesData | null>(null);
  const [strategies, setStrategies] = useState<StrategiesData | null>(null);
  const [approvals, setApprovals] = useState<ApprovalsData | null>(null);
  const [withdrawals, setWithdrawals] = useState<WithdrawalsData | null>(null);
  const [btcMarket, setBtcMarket] = useState<Record<string, unknown> | null>(null);
  const [ethMarket, setEthMarket] = useState<Record<string, unknown> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState<boolean>(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const [d, p, ex, t, s, a, w, btc, eth] = await Promise.all([
        TradingApi.getDashboard(effectiveToken),
        TradingApi.getPortfolio(effectiveToken),
        TradingApi.getExchangeStatus(effectiveToken).catch(() => null),
        TradingApi.getTrades(effectiveToken).catch(() => null),
        TradingApi.getStrategies(effectiveToken).catch(() => null),
        TradingApi.getApprovals(effectiveToken).catch(() => null),
        TradingApi.getWithdrawals(effectiveToken).catch(() => null),
        TradingApi.getMarket(effectiveToken, "BTC/USDT").catch(() => null),
        TradingApi.getMarket(effectiveToken, "ETH/USDT").catch(() => null),
      ]);
      setDashboard(d as DashboardData);
      setPortfolio(p as PortfolioData);
      if (ex) setExchangeStatus(ex as ExchangeStatusData);
      if (t) setTrades(t as TradesData);
      if (s) setStrategies(s as StrategiesData);
      if (a) setApprovals(a as ApprovalsData);
      if (w) setWithdrawals(w as WithdrawalsData);
      if (btc) setBtcMarket(btc as Record<string, unknown>);
      if (eth) setEthMarket(eth as Record<string, unknown>);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to fetch telemetry");
    } finally {
      setLoading(false);
    }
  }, [effectiveToken]);

  useEffect(() => {
    void refresh();
    const id = setInterval(() => {
      void refresh();
    }, 8000);
    return () => clearInterval(id);
  }, [refresh]);

  return {
    dashboard,
    portfolio,
    exchangeStatus,
    trades,
    strategies,
    approvals,
    withdrawals,
    btcMarket,
    ethMarket,
    error,
    loading,
    refresh,
  };
}
