"use client";

import React, { useState } from "react";
import { ShieldCheck } from "lucide-react";
import { login } from "@/services/api";

export function LoginPanel({
  onAuthenticated,
}: {
  onAuthenticated: (token: string, username: string, role: string) => void;
}) {
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      const res = await login(username, password);
      onAuthenticated(res.access_token, res.username, res.role);
    } catch (error) {
      setErr(error instanceof Error ? error.message : "Login failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-obsidian px-4">
      <form
        onSubmit={handleSubmit}
        className="w-full max-w-md rounded-xl border border-border bg-panel p-6 shadow-2xl"
      >
        <div className="mb-5 flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-emerald-500/15 text-emerald-400">
            <ShieldCheck className="h-5 w-5" />
          </div>
          <div>
            <h1 className="text-lg font-semibold text-white">Survival Trading Agent</h1>
            <p className="text-xs text-slate-400">Authenticated Operator Console</p>
          </div>
        </div>

        <label className="mb-1 block text-xs font-medium text-slate-300">Username</label>
        <input
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          className="mb-3 w-full rounded-lg border border-border bg-obsidian px-3 py-2 text-sm text-white outline-none focus:border-emerald-500"
          required
        />

        <label className="mb-1 block text-xs font-medium text-slate-300">Password</label>
        <input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          className="mb-4 w-full rounded-lg border border-border bg-obsidian px-3 py-2 text-sm text-white outline-none focus:border-emerald-500"
          required
        />

        {err && (
          <div className="mb-3 rounded border border-red-500/40 bg-red-500/10 px-3 py-2 text-xs text-red-300">
            {err}
          </div>
        )}

        <button
          type="submit"
          disabled={busy}
          className="w-full rounded-lg bg-emerald-600 px-4 py-2 text-sm font-semibold text-white transition hover:bg-emerald-500 disabled:opacity-50"
        >
          {busy ? "Authenticating..." : "Sign In"}
        </button>
      </form>
    </div>
  );
}
