import { motion } from "framer-motion";
import { Database, AlertCircle } from "lucide-react";
import { useLiveSnapshots } from "@/hooks/use-polymarket";
import { TableSkeleton, Badge } from "@/components/ui-elements";
import { formatRelativeTime, parseNumeric, formatPrice } from "@/lib/utils";
import { Link } from "wouter";

export default function Snapshots() {
  const { data, isLoading } = useLiveSnapshots({ limit: 100, executableOnly: true });

  return (
    <motion.div 
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      className="space-y-4"
    >
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4 mb-6">
        <div>
          <h2 className="text-2xl font-bold tracking-tight flex items-center gap-2">
            <Database className="text-primary" /> Live Opportunities
          </h2>
          <p className="text-sm text-muted-foreground font-mono mt-1">
            Latest state for markets with a bet-worthy signal (score ≥ 0.75, executable strategies)
          </p>
        </div>
        <div className="text-xs font-mono bg-accent border border-border px-3 py-1.5 rounded text-muted-foreground">
          Qualifying markets · latest 100
        </div>
      </div>

      <div className="terminal-panel">
        <div className="overflow-x-auto">
          <table className="w-full text-sm text-left whitespace-nowrap">
            <thead className="text-xs font-mono text-muted-foreground uppercase bg-muted/30 border-b border-border">
              <tr>
                <th className="px-4 py-3 font-medium">Polled At</th>
                <th className="px-4 py-3 font-medium">Market</th>
                <th className="px-4 py-3 font-medium text-right text-success">Yes Ask</th>
                <th className="px-4 py-3 font-medium text-right text-destructive">No Ask</th>
                <th className="px-4 py-3 font-medium text-right">Spread</th>
                <th className="px-4 py-3 font-medium text-center">Status</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border/50">
              {isLoading ? (
                <tr><td colSpan={6} className="p-4"><TableSkeleton /></td></tr>
              ) : !data || data.snapshots.length === 0 ? (
                <tr>
                  <td colSpan={6} className="px-4 py-12 text-center text-muted-foreground font-mono">
                    No markets currently meet the bet criteria.
                  </td>
                </tr>
              ) : (
                data.snapshots.map((snap) => (
                  <tr key={snap.id} className="data-row hover:bg-accent/40">
                    <td className="px-4 py-3 font-mono text-muted-foreground text-xs" title={snap.collectedAt || ""}>
                      {formatRelativeTime(snap.collectedAt)}
                    </td>
                    <td className="px-4 py-3 font-medium text-foreground max-w-[300px] truncate">
                      <Link href={`/markets/${snap.marketId}`} className="hover:text-primary transition-colors cursor-pointer">
                         {snap.question || snap.marketId}
                      </Link>
                    </td>
                    <td className="px-4 py-3 text-right font-mono font-bold text-success">
                      {formatPrice(snap.yesPrice)}
                    </td>
                    <td className="px-4 py-3 text-right font-mono font-bold text-destructive">
                      {formatPrice(snap.noPrice)}
                    </td>
                    <td className="px-4 py-3 text-right font-mono text-muted-foreground">
                      {parseNumeric(snap.spread).toFixed(4)}
                    </td>
                    <td className="px-4 py-3 text-center">
                       {(snap.errors && snap.errors.length > 0) ? (
                         <div className="inline-flex items-center gap-1 text-destructive bg-destructive/10 px-2 py-0.5 rounded text-[10px] uppercase font-mono border border-destructive/20" title={snap.errors.join(", ")}>
                           <AlertCircle size={10} /> Error
                         </div>
                       ) : (
                         <Badge className="bg-success/10 text-success border-success/20">OK</Badge>
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
