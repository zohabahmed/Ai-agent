"use client";

import React, { useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  KeyRound,
  Lock,
  RefreshCw,
  ShieldAlert,
  ShieldCheck,
} from "lucide-react";
import { TradingApi } from "@/services/api";
import type { ExchangeStatusData, PortfolioData } from "@/types/trading";

interface ExchangeSetupPanelProps {
  token: string;
  exchangeStatus: ExchangeStatusData | null;
  portfolio: PortfolioData | null;
  onRefresh: () => Promise<void>;
  onNotify: (msg: string) => void;
}

export function ExchangeSetupPanel({
  token,
  exchangeStatus,
  portfolio,
  onRefresh,
  onNotify,
}: ExchangeSetupPanelProps) {
  const [exchangeId, setExchangeId] = useState<string>(exchangeStatus?.exchange_id || "binance");
  const [apiKey, setApiKey] = useState<string>("");
  const [apiSecret, setApiSecret] = useState<string>("");
  const [apiPassword, setApiPassword] = useState<string>("");
  const [busy, setBusy] = useState<boolean>(false);

  const [riskAcknowledged, setRiskAcknowledged] = useState<boolean>(false);
  const [confirmPhrase, setConfirmPhrase] = useState<string>("");
  const [selectedMarket] = useState<"FUTURES">("FUTURES");
  const [selectedLeverage] = useState<number>(1);
  const [futuresAck, setFuturesAck] = useState<boolean>(false);

  const currentMode = exchangeStatus?.trading_mode || portfolio?.trading_mode || "LIVE_READ_ONLY";
  const currentMarket = exchangeStatus?.market_type || "FUTURES";
  const currentLev = exchangeStatus?.leverage || 1;
  const isVerified = Boolean(exchangeStatus?.verified);
  const canArmLive = isVerified && !exchangeStatus?.sandbox;
  const nonZeroBalances = (portfolio?.balances || []).filter(
    (b) => b.total > 0 || b.asset === "USDT"
  );

  async function handleSaveCredentials(e: React.FormEvent) {
    e.preventDefault();
    if (!apiKey.trim() || !apiSecret.trim()) {
      onNotify("Enter both your Exchange API Key and API Secret.");
      return;
    }
    setBusy(true);
    try {
      const res = (await TradingApi.saveExchangeCredentials(token, {
        exchange_id: exchangeId,
        api_key: apiKey.trim(),
        api_secret: apiSecret.trim(),
        api_password: apiPassword.trim(),
        sandbox: false,
        enter_read_only: true,
      })) as ExchangeStatusData;

      setApiKey("");
      setApiSecret("");
      setApiPassword("");
      await onRefresh();

      if (res.sync?.verified) {
        onNotify(
          `Connected ${res.exchange_id.toUpperCase()} (${res.api_key_mask}) in ${res.trading_mode}. Synced USDT: $${res.sync.total_quote.toFixed(4)}`
        );
      } else {
        onNotify(
          `Credentials saved (${res.api_key_mask}), but verification failed: ${res.sync?.error || res.last_error || "Check API permissions/IP"}`
        );
      }
    } catch (err) {
      onNotify(err instanceof Error ? err.message : "Failed to save exchange credentials.");
    } finally {
      setBusy(false);
    }
  }

  async function handleSyncBalances() {
    setBusy(true);
    try {
      const res = (await TradingApi.syncExchangeBalances(token)) as ExchangeStatusData;
      await onRefresh();
      onNotify(
        `Synced ${res.exchange_id.toUpperCase()} Futures balances in ${res.trading_mode}: $${(res.sync?.total_quote ?? 0).toFixed(4)} USDT`
      );
    } catch (err) {
      onNotify(err instanceof Error ? err.message : "Balance sync failed.");
    } finally {
      setBusy(false);
    }
  }

  async function handleModeSwitch(
    targetMode: "LIVE_READ_ONLY" | "LIVE_TRADING"
  ) {
    setBusy(true);
    try {
      await TradingApi.switchTradingMode(token, {
        target_mode: targetMode,
        risk_acknowledged: targetMode === "LIVE_TRADING" ? riskAcknowledged : false,
        confirmation_phrase: targetMode === "LIVE_TRADING" ? confirmPhrase : "",
      });
      setConfirmPhrase("");
      if (targetMode !== "LIVE_TRADING") {
        setRiskAcknowledged(false);
      }
      await onRefresh();
      onNotify(`Trading mode switched to ${targetMode}.`);
    } catch (err) {
      onNotify(err instanceof Error ? err.message : `Failed to switch to ${targetMode}.`);
    } finally {
      setBusy(false);
    }
  }

  async function handleMarketSwitch() {
    setBusy(true);
    try {
      await TradingApi.switchMarketType(token, {
        market_type: "FUTURES",
        leverage: 1,
        futures_acknowledged: futuresAck,
      });
      await onRefresh();
      onNotify("Confirmed USD-M FUTURES (1x ISOLATED).");
    } catch (err) {
      onNotify(err instanceof Error ? err.message : "Failed to switch market type.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="rounded-xl border border-sky-500/30 bg-panel p-4 space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <KeyRound className="h-4 w-4 text-sky-400" />
          <h2 className="text-xs font-semibold uppercase tracking-wider text-slate-200">
            Exchange Connection & Real USDT Sync
          </h2>
          {isVerified ? (
            <span className="inline-flex items-center gap-1 rounded border border-emerald-500/40 bg-emerald-500/15 px-2 py-0.5 text-[11px] font-semibold text-emerald-300">
              <CheckCircle2 className="h-3 w-3" /> VERIFIED ({exchangeStatus?.exchange_id?.toUpperCase()}{" "}
              {exchangeStatus?.api_key_mask})
            </span>
          ) : (
            <span className="inline-flex items-center gap-1 rounded border border-amber-500/40 bg-amber-500/15 px-2 py-0.5 text-[11px] font-semibold text-amber-300">
              <AlertTriangle className="h-3 w-3" />{" "}
              {exchangeStatus?.credentials_configured ? "UNVERIFIED" : "NO API KEY CONFIGURED"}
            </span>
          )}
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            disabled={busy || !exchangeStatus?.credentials_configured}
            onClick={handleSyncBalances}
            className="inline-flex items-center gap-1.5 rounded-lg border border-sky-500/40 bg-sky-500/15 px-3 py-1.5 text-xs font-semibold text-sky-200 hover:bg-sky-500/25 disabled:opacity-40"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${busy ? "animate-spin" : ""}`} />
            Sync Futures Balances
          </button>
        </div>
      </div>

      <p className="text-xs text-slate-400">
        Your real USDT stays in your{" "}
        <strong className="text-slate-200">{(exchangeStatus?.exchange_id || exchangeId).toUpperCase()} USD-M Futures Wallet</strong>. Create an API Key with{" "}
        <span className="text-emerald-300">Enable Reading + Enable Futures</span> enabled and keep{" "}
        <span className="text-red-300">Withdrawals DISABLED</span>. Trades any positive USDT balance cleanly
        (1x Isolated default, with auto-minimal 2x–5x isolated scaling when below a venue order floor, or native 1x $1+ on MEXC / KuCoin Futures / Gate).
      </p>

      {exchangeStatus?.last_error && (
        <div className="rounded-lg border border-red-500/40 bg-red-950/30 px-3 py-2 text-xs text-red-300">
          Last Exchange Error: {exchangeStatus.last_error}
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-2">
        {/* Step 1: API Key Form */}
        <form
          onSubmit={handleSaveCredentials}
          className="rounded-lg border border-border/70 bg-obsidian p-3.5 space-y-3"
        >
          <div className="flex items-center justify-between">
            <span className="text-xs font-semibold text-white">
              Step 1 · Connect Futures Exchange API (Enters LIVE_READ_ONLY)
            </span>
            <Lock className="h-3.5 w-3.5 text-slate-400" />
          </div>

          <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2 text-xs">
            <div>
              <label className="mb-1 block text-slate-400">Exchange</label>
              <select
                value={exchangeId}
                onChange={(e) => setExchangeId(e.target.value)}
                className="w-full rounded-lg border border-border bg-panel px-2.5 py-1.5 text-white outline-none focus:border-sky-500"
              >
                {(
                  exchangeStatus?.supported_exchanges || [
                    "binance",
                    "bybit",
                    "okx",
                    "kraken",
                    "mexc",
                    "kucoinfutures",
                    "gate",
                  ]
                ).map((ex) => (
                  <option key={ex} value={ex}>
                    {ex.toUpperCase()}
                    {ex === "mexc" || ex === "kucoinfutures" || ex === "gate" ? " ($1+ Live Futures)" : ""}
                  </option>
                ))}
              </select>
            </div>
            <div className="flex items-end pb-1.5">
              <span className="text-[11px] text-emerald-300/90">
                Real-USDT Live Futures Account Only (1x Isolated)
              </span>
            </div>
          </div>

          <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2 text-xs">
            <div>
              <label className="mb-1 block text-slate-400">API Key</label>
              <input
                type="password"
                autoComplete="off"
                placeholder="Paste Binance API Key"
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                className="w-full rounded-lg border border-border bg-panel px-2.5 py-1.5 font-mono text-white outline-none focus:border-sky-500"
              />
            </div>
            <div>
              <label className="mb-1 block text-slate-400">API Secret</label>
              <input
                type="password"
                autoComplete="off"
                placeholder="Paste Binance API Secret"
                value={apiSecret}
                onChange={(e) => setApiSecret(e.target.value)}
                className="w-full rounded-lg border border-border bg-panel px-2.5 py-1.5 font-mono text-white outline-none focus:border-sky-500"
              />
            </div>
          </div>

          {(exchangeId === "okx" || exchangeId === "kucoinfutures") && (
            <div className="text-xs">
              <label className="mb-1 block text-slate-400">API Passphrase ({exchangeId.toUpperCase()})</label>
              <input
                type="password"
                autoComplete="off"
                value={apiPassword}
                onChange={(e) => setApiPassword(e.target.value)}
                className="w-full rounded-lg border border-border bg-panel px-2.5 py-1.5 font-mono text-white outline-none focus:border-sky-500"
              />
            </div>
          )}

          <div className="flex flex-wrap items-center justify-between gap-2 pt-1">
            <div className="flex flex-wrap gap-1.5 text-[11px]">
              {nonZeroBalances.map((b) => (
                <span
                  key={b.asset}
                  className="rounded border border-border bg-panel px-2 py-0.5 font-mono text-slate-200"
                >
                  {b.asset}: {b.total.toFixed(b.asset === "USDT" ? 2 : 6)}
                </span>
              ))}
            </div>
            <button
              type="submit"
              disabled={busy}
              className="rounded-lg bg-sky-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-sky-500 disabled:opacity-40"
            >
              Connect & Verify (Read-Only)
            </button>
          </div>
        </form>

        {/* Step 2: Live Trading Confirmation Gate */}
        <div className="rounded-lg border border-border/70 bg-obsidian p-3.5 space-y-3 text-xs">
          <div className="flex items-center justify-between">
            <span className="font-semibold text-white">
              Step 2 · Live Execution Gate ({currentMode})
            </span>
            {currentMode === "LIVE_TRADING" && exchangeStatus?.live_trading_authorized ? (
              <ShieldCheck className="h-4 w-4 text-emerald-400" />
            ) : (
              <ShieldAlert className="h-4 w-4 text-amber-400" />
            )}
          </div>

          {currentMode === "LIVE_TRADING" && exchangeStatus?.live_trading_authorized ? (
            <div className="space-y-3 rounded-lg border border-emerald-500/40 bg-emerald-950/25 p-3">
              <div className="font-semibold text-emerald-300">
                LIVE_TRADING ARMED · Real {(exchangeStatus?.exchange_id || exchangeId).toUpperCase()} USD-M Futures orders are authorized within Survival limits
                (1x Isolated Futures only, max 60m hold, withdrawals disabled).
              </div>
              <button
                type="button"
                disabled={busy}
                onClick={() => handleModeSwitch("LIVE_READ_ONLY")}
                className="rounded-lg border border-amber-500/40 bg-amber-500/15 px-3 py-1.5 font-semibold text-amber-200 hover:bg-amber-500/25 disabled:opacity-40"
              >
                Disarm to LIVE_READ_ONLY
              </button>
            </div>
          ) : (
            <div className="space-y-2.5">
              <div className="text-slate-400">
                {canArmLive
                  ? "Credentials verified. Review survival limits and explicitly confirm before enabling real orders."
                  : "Verify non-sandbox Exchange credentials in Step 1 first to verify your real USDT balance before arming LIVE_TRADING."}
              </div>
              <label className="flex items-start gap-2 text-slate-300 cursor-pointer">
                <input
                  type="checkbox"
                  disabled={!canArmLive || busy}
                  checked={riskAcknowledged}
                  onChange={(e) => setRiskAcknowledged(e.target.checked)}
                  className="mt-0.5 rounded border-border bg-panel"
                />
                <span>
                  I understand USD-M Futures Isolated orders will use real USDT margin on{" "}
                  {(exchangeStatus?.exchange_id || exchangeId).toUpperCase()} within Max Risk/Trade and
                  Drawdown gates (1x default; minimal 2x–5x isolated auto-scaling only when needed below a venue order floor).
                </span>
              </label>
              <div className="flex flex-wrap items-center gap-2">
                <input
                  type="text"
                  disabled={!canArmLive || busy}
                  placeholder='Type "ENABLE LIVE TRADING"'
                  value={confirmPhrase}
                  onChange={(e) => setConfirmPhrase(e.target.value)}
                  className="flex-1 min-w-[180px] rounded-lg border border-border bg-panel px-2.5 py-1.5 font-mono text-white outline-none focus:border-red-500 disabled:opacity-40"
                />
                <button
                  type="button"
                  disabled={
                    !canArmLive ||
                    busy ||
                    !riskAcknowledged ||
                    confirmPhrase.trim().toUpperCase() !== "ENABLE LIVE TRADING"
                  }
                  onClick={() => handleModeSwitch("LIVE_TRADING")}
                  className="rounded-lg bg-red-600 px-3 py-1.5 font-semibold text-white hover:bg-red-500 disabled:opacity-40"
                >
                  Authorize LIVE_TRADING
                </button>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Step 3: USD-M Futures (1x Isolated Only) */}
      <div className="rounded-lg border border-border/70 bg-obsidian p-3.5 space-y-2.5 text-xs">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <span className="font-semibold text-white">
            Market & Leverage Policy · Active: {currentMarket} ({currentLev}x ISOLATED USDT-M · Long & Short)
          </span>
          <span className="text-[11px] text-slate-400">
            {(exchangeStatus?.exchange_id || exchangeId).toUpperCase()} Futures requires &ldquo;Enable Futures&rdquo; on your API key + USDT in Futures wallet
          </span>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <span className="rounded-lg border border-amber-500/40 bg-amber-500/10 px-2.5 py-1.5 font-mono font-semibold text-amber-200">
            USD-M Perpetual Futures · {selectedLeverage}x Isolated (Long & Short)
          </span>
          <label className="inline-flex items-center gap-1.5 text-slate-300 cursor-pointer">
            <input
              type="checkbox"
              checked={futuresAck}
              disabled={busy}
              onChange={(e) => setFuturesAck(e.target.checked)}
              className="rounded border-border bg-panel"
            />
            I accept {selectedMarket} 1x Isolated Futures & funding-rate policy
          </label>
          <button
            type="button"
            disabled={busy || !futuresAck}
            onClick={handleMarketSwitch}
            className="rounded-lg border border-sky-500/40 bg-sky-500/15 px-3 py-1.5 font-semibold text-sky-200 hover:bg-sky-500/25 disabled:opacity-40"
          >
            Confirm 1x Futures Policy
          </button>
        </div>
      </div>
    </section>
  );
}
