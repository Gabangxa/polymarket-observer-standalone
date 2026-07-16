import { useState } from "react";
import { motion } from "framer-motion";
import { Filter, Zap } from "lucide-react";
import { useLiveSignals } from "@/hooks/use-polymarket";
import { TableSkeleton, Badge } from "@/components/ui-elements";
import { formatRelativeTime, getStrategyColor, parseNumeric, formatPrice } from "@/lib/utils";
import { Link } from "wouter";

export default function Signals() {
  const [strategyFilter, setStrategyFilter] = useState<string>("");
  const { data, isLoading } = useLiveSignals({
    limit: 200,
    hours: 168,
    strategy: strategyFilter || undefined,
    executableOnly: true,
  });

  // Only the executable strategies can clear the bet bar, so the filter is
  // scoped to them. Values MUST match the `strategy` strings emitted by the
  // engines (bot/agents/*_engine.py), not display slugs.
  const strategies: { value: string; label: string }[] = [
    { value: "",                  label: "ALL" },
    { value: "spread_engine",     label: "Spread" },
    { value: "tail_yield_engine", label: "Tail Yield" },
  ];

  return (
    <motion.div 
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      className="space-y-4"
    >
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4 mb-6">
        <div>
          <h2 className="text-2xl font-bold tracking-tight flex items-center gap-2">
            <Zap className="text-primary" /> Strategy Signals
          </h2>
          <p className="text-sm text-muted-foreground font-mono mt-1">
            Bet-worthy opportunities (score ≥ 0.75, executable strategies) · last 7 days
          </p>
        </div>
        
        <div className="flex bg-card border border-border p-1 rounded-md">
          {strategies.map(({ value, label }) => (
            <button
              key={value}
              onClick={() => setStrategyFilter(value)}
              className={`px-3 py-1.5 text-xs font-mono rounded transition-colors ${
                strategyFilter === value
                  ? "bg-primary text-primary-foreground font-bold"
                  : "text-muted-foreground hover:bg-accent hover:text-foreground"
              }`}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      <div className="terminal-panel">
        <div className="overflow-x-auto">
          <table className="w-full text-sm text-left whitespace-nowrap">
            <thead className="text-xs font-mono text-muted-foreground uppercase bg-muted/30 border-b border-border">
              <tr>
                <th className="px-4 py-3 font-medium">Time</th>
                <th className="px-4 py-3 font-medium">Strategy</th>
                <th className="px-4 py-3 font-medium">Target Market</th>
                <th className="px-4 py-3 font-medium">Trigger</th>
                <th className="px-4 py-3 font-medium text-right">Score</th>
                <th className="px-4 py-3 font-medium text-right">Entry Price</th>
                <th className="px-4 py-3 font-medium text-center">Status</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border/50">
              {isLoading ? (
                <tr><td colSpan={7} className="p-4"><TableSkeleton /></td></tr>
              ) : !data || data.signals.length === 0 ? (
                <tr>
                  <td colSpan={7} className="px-4 py-12 text-center text-muted-foreground font-mono">
                    <div className="flex flex-col items-center gap-2">
                      <Filter size={24} className="opacity-20" />
                      No signals meet the bet criteria for this filter.
                    </div>
                  </td>
                </tr>
              ) : (
                data.signals.map((signal) => (
                  <tr key={signal.id} className="data-row">
                    <td className="px-4 py-4 font-mono text-muted-foreground text-xs" title={signal.emittedAt || ""}>
                      {formatRelativeTime(signal.emittedAt)}
                    </td>
                    <td className="px-4 py-4">
                      <Badge className={getStrategyColor(signal.strategy)}>
                        {signal.strategy}
                      </Badge>
                    </td>
                    <td className="px-4 py-4 font-medium text-foreground max-w-[300px] truncate">
                      {signal.marketId ? (
                         <Link href={`/markets/${signal.marketId}`} className="hover:text-primary transition-colors cursor-pointer">
                           {signal.question || signal.eventSlug || signal.marketId}
                         </Link>
                      ) : (
                        signal.eventSlug || "Unknown"
                      )}
                    </td>
                    <td className="px-4 py-4 max-w-[360px] whitespace-normal">
                      {(() => {
                        const meta = signal.metadata as Record<string, unknown> | null | undefined;
                        const text = (meta?.trigger ?? meta?.note) as string | undefined;
                        return text ? (
                          <span
                            className="font-mono text-xs text-muted-foreground leading-relaxed"
                            title={text}
                          >
                            {text.length > 120 ? text.slice(0, 117) + "…" : text}
                          </span>
                        ) : (
                          <span className="text-xs text-muted-foreground/40">—</span>
                        );
                      })()}
                    </td>
                    <td className="px-4 py-4 text-right font-mono text-primary font-bold">
                      {signal.signalScore ? parseNumeric(signal.signalScore).toFixed(2) : "-"}
                    </td>
                    <td className="px-4 py-4 text-right font-mono text-foreground">
                       {signal.entryPrice ? formatPrice(signal.entryPrice) : "-"}
                    </td>
                    <td className="px-4 py-4 text-center">
                       {signal.resolved ? (
                         <Badge className="bg-muted text-muted-foreground border-border">Resolved</Badge>
                       ) : (
                         <Badge className="bg-primary/20 text-primary border-primary/30 animate-pulse">Active</Badge>
                       )}
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
