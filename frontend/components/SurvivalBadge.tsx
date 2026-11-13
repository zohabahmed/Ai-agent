"use client";

import React from "react";

const STATE_STYLES: Record<string, string> = {
  NORMAL: "bg-emerald-500/15 text-emerald-400 border-emerald-500/40",
  CAUTION: "bg-amber-500/15 text-amber-300 border-amber-500/40",
  DEFENSIVE: "bg-orange-500/15 text-orange-300 border-orange-500/40",
  HALTED: "bg-red-500/20 text-red-400 border-red-500/50",
  RECOVERY: "bg-sky-500/15 text-sky-300 border-sky-500/40",
};

export function SurvivalBadge({ state }: { state: string }) {
  const cls = STATE_STYLES[state] || "bg-slate-700/30 text-slate-300 border-slate-600";
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-semibold tracking-wide ${cls}`}>
      <span className="h-2 w-2 rounded-full bg-current" />
      {state}
    </span>
  );
}
