import { motion } from "framer-motion";
import { TrendingUp, Trophy, BarChart3, AlertTriangle } from "lucide-react";
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Cell,
} from "recharts";
import { useLiveSignals, useStrategyPerformance } from "@/hooks/use-polymarket";
import { StatCard, Badge, TableSkeleton } from "@/components/ui-elements";
import { getStrategyColor, parseNumeric, formatInTz } from "@/lib/utils";
import { useTimezone } from "@/hooks/use-timezone";

// ── Helpers ──────────────────────────────────────────────────────────────────

function fmtWinRate(rate: number | string | null | undefined): string {
  if (rate === null || rate === undefined) return "—";
  const n = parseNumeric(rate);
  return `${(n * 100).toFixed(1)}%`;
}

function fmtPnl(pnl: number | string | null | undefined): string {
  if (pnl === null || pnl === undefined) return "—";
  const n = parseNumeric(pnl);
  return n >= 0 ? `+${n.toFixed(4)}` : n.toFixed(4);
}

function winRateColor(rate: number | string | null | undefined): string {
  if (rate === null || rate === undefined) return "text-muted-foreground";
  const n = parseNumeric(rate);
  if (n >= 0.6) return "text-success";
  if (n >= 0.4) return "text-yellow-400";
  return "text-destructive";
}

// Build daily accuracy trend from raw signals data
function buildTrend(signals: any[], tz: string) {
  const resolved = signals.filter((s) => s.resolved);
  const byDay: Record<string, { wins: number; total: number }> = {};

  for (const s of resolved) {
    if (!s.emittedAt) continue;
    const day = formatInTz(s.emittedAt, "MMM dd", tz);
    if (!byDay[day]) byDay[day] = { wins: 0, total: 0 };
    byDay[day].total += 1;
    if (s.outcome === true) byDay[day].wins += 1;
  }

  return Object.entries(byDay)
    .map(([day, { wins, total }]) => ({
      day,
      winRate: total > 0 ? Math.round((wins / total) * 100) : 0,
      total,
    }))
    .slice(-14); // last 14 days
}

const STRATEGY_LABELS: Record<string, string> = {
  spread_harvesting:  "Spread",
  neg_risk_overround: "Neg Risk",
  mean_reversion:     "Reversion",
};

// ── Component ─────────────────────────────────────────────────────────────────

export default function Performance() {
  const { timezone } = useTimezone();
  const { data: perf, isLoading: perfLoading, isError: perfError } = useStrategyPerformance();
  const { data: signalsData, isLoading: signalsLoading } = useLiveSignals({ hours: 168, limit: 500 });

  const trendData = signalsData ? buildTrend(signalsData.signals, timezone) : [];

  // Aggregate totals for summary cards
  const totalSignals   = perf?.strategies.reduce((a, s) => a + s.signalCount, 0) ?? 0;
  const totalResolved  = perf?.strategies.reduce((a, s) => a + s.resolvedCount, 0) ?? 0;
  const totalWins      = perf?.strategies.reduce((a, s) => a + s.winCount, 0) ?? 0;
  const overallWinRate = totalResolved > 0 ? totalWins / totalResolved : null;

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      className="space-y-6 pb-12"
    >
      {/* Header */}
      <div className="flex items-center gap-3 mb-2">
        <TrendingUp className="text-primary" size={24} />
        <div>
          <h2 className="text-2xl font-bold tracking-tight">Strategy Performance</h2>
          <p className="text-sm text-muted-foreground font-mono mt-0.5">
            Outcome tracking across all resolved paper-trade signals
          </p>
        </div>
      </div>

      {/* Summary cards */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard title="Total Signals"   value={totalSignals.toString()} />
        <StatCard title="Resolved"        value={totalResolved.toString()} subtitle={`${totalSignals - totalResolved} still open`} />
        <StatCard
          title="Overall Win Rate"
          value={fmtWinRate(overallWinRate)}
          className={overallWinRate !== null && overallWinRate >= 0.5 ? "border-success/30 bg-success/5" : "border-destructive/30 bg-destructive/5"}
        />
        <StatCard title="Wins"            value={totalWins.toString()} subtitle={`of ${totalResolved} resolved`} />
      </div>

      {/* Strategy scorecard */}
      <div className="terminal-panel">
        <div className="terminal-header">
          <span className="flex items-center gap-2"><Trophy size={14} /> Strategy Scorecard</span>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm text-left whitespace-nowrap">
            <thead className="text-xs font-mono text-muted-foreground uppercase bg-muted/30 border-b border-border">
              <tr>
                <th className="px-4 py-3 font-medium">Strategy</th>
                <th className="px-4 py-3 font-medium text-right">Signals</th>
                <th className="px-4 py-3 font-medium text-right">Resolved</th>
                <th className="px-4 py-3 font-medium text-right">Wins</th>
                <th className="px-4 py-3 font-medium text-right">Win Rate</th>
                <th className="px-4 py-3 font-medium text-right">Avg PnL</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border/50">
              {perfLoading ? (
                <tr><td colSpan={6} className="p-4"><TableSkeleton /></td></tr>
              ) : perfError || !perf ? (
                <tr>
                  <td colSpan={6} className="px-4 py-10 text-center text-muted-foreground font-mono">
                    <div className="flex flex-col items-center gap-2">
                      <AlertTriangle size={24} className="opacity-30" />
                      Performance data unavailable
                    </div>
                  </td>
                </tr>
              ) : perf.strategies.length === 0 ? (
                <tr>
                  <td colSpan={6} className="px-4 py-10 text-center text-muted-foreground font-mono">
                    No resolved signals yet — check back after the first resolution window expires.
                  </td>
                </tr>
              ) : (
                perf.strategies.map((row) => (
                  <tr key={row.strategy} className="data-row">
                    <td className="px-4 py-4">
                      <Badge className={getStrategyColor(row.strategy)}>
                        {row.strategy}
                      </Badge>
                    </td>
                    <td className="px-4 py-4 text-right font-mono text-foreground">{row.signalCount}</td>
                    <td className="px-4 py-4 text-right font-mono text-muted-foreground">{row.resolvedCount}</td>
                    <td className="px-4 py-4 text-right font-mono text-foreground">{row.winCount}</td>
                    <td className={`px-4 py-4 text-right font-mono font-bold ${winRateColor(row.winRate)}`}>
                      {fmtWinRate(row.winRate)}
                    </td>
                    <td className={`px-4 py-4 text-right font-mono ${row.avgPnl !== null && row.avgPnl >= 0 ? "text-success" : "text-destructive"}`}>
                      {fmtPnl(row.avgPnl)}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Accuracy trend chart */}
      <div className="terminal-panel p-1">
        <div className="terminal-header">
          <span className="flex items-center gap-2"><BarChart3 size={14} /> Daily Accuracy Trend (last 14 days)</span>
        </div>
        <div className="p-4 h-[280px] w-full">
          {signalsLoading ? (
            <div className="h-full flex items-center justify-center text-muted-foreground font-mono text-sm animate-pulse">
              Loading trend data...
            </div>
          ) : trendData.length === 0 ? (
            <div className="h-full flex items-center justify-center text-muted-foreground font-mono text-sm">
              No resolved signals in the last 14 days
            </div>
          ) : (
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={trendData} margin={{ top: 10, right: 10, left: -20, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#27272a" vertical={false} />
                <XAxis
                  dataKey="day"
                  stroke="#71717a"
                  fontSize={12}
                  tickLine={false}
                  axisLine={false}
                />
                <YAxis
                  stroke="#71717a"
                  fontSize={12}
                  tickLine={false}
                  axisLine={false}
                  domain={[0, 100]}
                  tickFormatter={(v) => `${v}%`}
                />
                <Tooltip
                  contentStyle={{
                    backgroundColor: "#0a0a0a",
                    borderColor: "#27272a",
                    color: "#f4f4f5",
                    fontFamily: "monospace",
                  }}
                  formatter={(value: number, _: string, entry: any) =>
                    [`${value}% (${entry.payload.total} resolved)`, "Win Rate"]
                  }
                />
                <Bar dataKey="winRate" radius={[3, 3, 0, 0]}>
                  {trendData.map((entry, i) => (
                    <Cell
                      key={i}
                      fill={entry.winRate >= 50 ? "hsl(var(--success))" : "hsl(var(--destructive))"}
                    />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          )}
        </div>
      </div>

      {/* Category breakdown */}
      <div className="terminal-panel">
        <div className="terminal-header">
          <span>Performance by Market Category</span>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm text-left whitespace-nowrap">
            <thead className="text-xs font-mono text-muted-foreground uppercase bg-muted/30 border-b border-border">
              <tr>
                <th className="px-4 py-3 font-medium">Category</th>
                <th className="px-4 py-3 font-medium">Strategy</th>
                <th className="px-4 py-3 font-medium text-right">Signals</th>
                <th className="px-4 py-3 font-medium text-right">Resolved</th>
                <th className="px-4 py-3 font-medium text-right">Win Rate</th>
                <th className="px-4 py-3 font-medium text-right">Avg PnL</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border/50">
              {perfLoading ? (
                <tr><td colSpan={6} className="p-4"><TableSkeleton /></td></tr>
              ) : !perf || perf.categories.length === 0 ? (
                <tr>
                  <td colSpan={6} className="px-4 py-10 text-center text-muted-foreground font-mono">
                    No category data yet
                  </td>
                </tr>
              ) : (
                perf.categories.map((row, i) => (
                  <tr key={i} className="data-row">
                    <td className="px-4 py-4 font-mono text-xs text-muted-foreground uppercase tracking-wider">
                      {row.category}
                    </td>
                    <td className="px-4 py-4">
                      <Badge className={getStrategyColor(row.strategy)}>
                        {STRATEGY_LABELS[row.strategy] ?? row.strategy}
                      </Badge>
                    </td>
                    <td className="px-4 py-4 text-right font-mono text-foreground">{row.signalCount}</td>
                    <td className="px-4 py-4 text-right font-mono text-muted-foreground">{row.resolvedCount}</td>
                    <td className={`px-4 py-4 text-right font-mono font-bold ${winRateColor(row.winRate)}`}>
                      {fmtWinRate(row.winRate)}
                    </td>
                    <td className={`px-4 py-4 text-right font-mono ${row.avgPnl !== null && row.avgPnl >= 0 ? "text-success" : "text-destructive"}`}>
                      {fmtPnl(row.avgPnl)}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>
    </motion.div>
  );
}
