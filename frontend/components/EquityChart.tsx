"use client";

import React from "react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

interface EquityPoint {
  timestamp: string;
  total_equity: number;
  realized_pnl: number;
  drawdown_pct: number;
}

export function EquityChart({ data }: { data: EquityPoint[] }) {
  if (!data || data.length === 0) {
    return (
      <div className="flex h-56 items-center justify-center rounded-lg border border-border/60 bg-obsidian/60 text-xs text-slate-400">
        No portfolio snapshots recorded yet. Run a trading cycle to populate real equity telemetry.
      </div>
    );
  }

  const formatted = data.map((pt) => ({
    time: new Date(pt.timestamp).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
    equity: Number(pt.total_equity.toFixed(4)),
    drawdownPct: Number((pt.drawdown_pct * 100).toFixed(2)),
  }));

  return (
    <div className="h-56 w-full">
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={formatted}>
          <defs>
            <linearGradient id="eqGrad" x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%" stopColor="#10B981" stopOpacity={0.35} />
              <stop offset="95%" stopColor="#10B981" stopOpacity={0.0} />
            </linearGradient>
          </defs>
          <CartesianGrid strokeDasharray="3 3" stroke="#1E293B" />
          <XAxis dataKey="time" stroke="#64748B" fontSize={11} />
          <YAxis stroke="#64748B" fontSize={11} domain={["auto", "auto"]} />
          <Tooltip
            contentStyle={{
              backgroundColor: "#0E1522",
              borderColor: "#1E293B",
              color: "#E2E8F0",
              fontSize: "12px",
            }}
          />
          <Area
            type="monotone"
            dataKey="equity"
            stroke="#10B981"
            strokeWidth={2}
            fill="url(#eqGrad)"
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
