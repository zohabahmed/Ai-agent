const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE_URL ||
  process.env.NEXT_PUBLIC_API_URL ||
  "http://localhost:8000/api";

async function request<T>(
  path: string,
  token: string = "direct-operator",
  options: RequestInit = {}
): Promise<T> {
  const effectiveToken = token || "direct-operator";
  const res = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${effectiveToken}`,
      ...(options.headers || {}),
    },
    cache: "no-store",
  });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const data = await res.json();
      detail = data.detail || JSON.stringify(data);
    } catch {
      detail = await res.text();
    }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

export async function login(
  username: string,
  password: string
): Promise<{ access_token: string; token_type: string; username: string; role: string }> {
  const form = new URLSearchParams();
  form.set("username", username);
  form.set("password", password);
  const res = await fetch(`${API_BASE}/auth/token`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: form.toString(),
  });
  if (!res.ok) {
    throw new Error("Invalid operator credentials");
  }
  return res.json();
}

export const TradingApi = {
  getDashboard: (token: string) => request("/dashboard", token),
  getPortfolio: (token: string) => request("/portfolio", token),
  getExchangeStatus: (token: string) => request("/exchange/status", token),
  getTrades: (token: string) => request("/trades", token),
  getStrategies: (token: string) => request("/strategies", token),
  getApprovals: (token: string) => request("/approvals", token),
  getWithdrawals: (token: string) => request("/withdrawals", token),
  getMarket: (
    token: string,
    symbol: string,
    timeframe: string = "15m",
    limit: number = 96
  ) =>
    request(
      `/market/${symbol.replace("/", "-")}?timeframe=${encodeURIComponent(
        timeframe
      )}&limit=${limit}`,
      token
    ),
  startTrading: (token: string) =>
    request("/trading/start", token, { method: "POST" }),
  stopTrading: (token: string) =>
    request("/trading/stop", token, { method: "POST" }),
  runCycleNow: (token: string, timeframe: string = "15m") =>
    request(`/trading/run-cycle?timeframe=${encodeURIComponent(timeframe)}`, token, {
      method: "POST",
    }),
  emergencyStop: (token: string, reason: string) =>
    request("/trading/emergency-stop", token, {
      method: "POST",
      body: JSON.stringify({ reason }),
    }),
  resetRecovery: (token: string) =>
    request("/trading/reset-recovery", token, { method: "POST" }),
  saveExchangeCredentials: (
    token: string,
    payload: {
      exchange_id: string;
      api_key: string;
      api_secret: string;
      api_password?: string;
      sandbox: boolean;
      enter_read_only: boolean;
    }
  ) =>
    request("/exchange/credentials", token, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  syncExchangeBalances: (token: string) =>
    request("/exchange/sync-balances", token, { method: "POST" }),
  switchTradingMode: (
    token: string,
    payload: {
      target_mode: "LIVE_READ_ONLY" | "LIVE_TRADING";
      risk_acknowledged: boolean;
      confirmation_phrase: string;
    }
  ) =>
    request("/exchange/mode", token, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  switchMarketType: (
    token: string,
    payload: {
      market_type: "SPOT" | "FUTURES";
      leverage: number;
      futures_acknowledged: boolean;
    }
  ) =>
    request("/exchange/market-type", token, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  approveOperation: (token: string, approvalId: string, comment: string = "Approved via UI") =>
    request(`/approvals/${approvalId}/approve`, token, {
      method: "POST",
      body: JSON.stringify({ comment }),
    }),
  rejectOperation: (token: string, approvalId: string, comment: string = "Rejected via UI") =>
    request(`/approvals/${approvalId}/reject`, token, {
      method: "POST",
      body: JSON.stringify({ comment }),
    }),
  getActiveTrades: (token: string = "direct-operator") =>
    request("/trading/active-trades", token),
  closePosition: (token: string, positionId: string, fraction: number = 1) =>
    request(`/trading/positions/${positionId}/close?fraction=${fraction}`, token, {
      method: "POST",
    }),
  closeAllPositions: (token: string = "direct-operator") =>
    request("/trading/positions/close-all", token, { method: "POST" }),
  getScanner: (
    token: string = "direct-operator",
    timeframe: string = "15m",
    scope: string = "GLOBAL",
    q: string = ""
  ) =>
    request(
      `/market-scanner?timeframe=${encodeURIComponent(timeframe)}&scope=${encodeURIComponent(scope)}${
        q ? `&q=${encodeURIComponent(q)}` : ""
      }`,
      token
    ),
  searchGlobalMarkets: (
    token: string = "direct-operator",
    q: string = "",
    limit: number = 50
  ) =>
    request(
      `/markets/search?q=${encodeURIComponent(q)}&limit=${limit}`,
      token
    ),
  getWatchlist: (token: string = "direct-operator") =>
    request("/markets/watchlist", token),
  updateWatchlist: (
    token: string = "direct-operator",
    symbol: string,
    action: "add" | "remove" = "add"
  ) =>
    request("/markets/watchlist", token, {
      method: "POST",
      body: JSON.stringify({ symbol, action }),
    }),
  openTrade: (token: string, symbol: string, side?: "LONG" | "SHORT", timeframe: string = "15m") =>
    request(
      `/trading/manual-trade?symbol=${encodeURIComponent(symbol)}&timeframe=${encodeURIComponent(
        timeframe
      )}${side ? `&side=${side}` : ""}`,
      token,
      { method: "POST" }
    ),
  updatePositionTpSl: (
    token: string,
    positionId: string,
    payload: { stop_loss?: number | null; take_profit?: number | null }
  ) =>
    request(`/trading/positions/${positionId}/tpsl`, token, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  exportTradesHistoryBlob: async (
    token: string = "direct-operator",
    format: "csv" | "json" = "csv",
    scope: "closed" | "executions" | "all" = "closed"
  ): Promise<{ blob: Blob; filename: string }> => {
    const effectiveToken = token || "direct-operator";
    const res = await fetch(
      `${API_BASE}/trading/trades/export?format=${format}&scope=${scope}`,
      {
        headers: {
          Authorization: `Bearer ${effectiveToken}`,
        },
        cache: "no-store",
      }
    );
    if (!res.ok) {
      let detail = `HTTP ${res.status}`;
      try {
        const data = await res.json();
        detail = data.detail || JSON.stringify(data);
      } catch {
        detail = await res.text();
      }
      throw new Error(detail || "Failed to download trades history");
    }
    const disposition = res.headers.get("Content-Disposition") || "";
    let filename = `survivalx_trade_history_${new Date().toISOString().slice(0, 10)}.${format}`;
    const match = disposition.match(/filename="?([^";]+)"?/i);
    if (match && match[1]) {
      filename = match[1].trim();
    }
    const blob = await res.blob();
    return { blob, filename };
  },
  downloadTradesHistory: async (
    token: string = "direct-operator",
    format: "csv" | "json" = "csv",
    scope: "closed" | "executions" | "all" = "closed"
  ): Promise<string> => {
    const { blob, filename } = await TradingApi.exportTradesHistoryBlob(token, format, scope);
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    window.URL.revokeObjectURL(url);
    document.body.removeChild(a);
    return filename;
  },
};
