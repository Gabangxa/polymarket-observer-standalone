import { useState } from "react";
import { motion } from "framer-motion";
import { useLocation } from "wouter";
import { Search, Filter, ArrowUpDown } from "lucide-react";
import { useLiveMarkets } from "@/hooks/use-polymarket";
import { TableSkeleton, Badge } from "@/components/ui-elements";
import { formatCurrency, formatPercent, parseNumeric, formatInTz } from "@/lib/utils";
import { useTimezone } from "@/hooks/use-timezone";

export default function Markets() {
  const [, setLocation] = useLocation();
  const { data, isLoading } = useLiveMarkets();
  const [search, setSearch] = useState("");
  const { timezone } = useTimezone();

  const filteredMarkets = data?.markets.filter(m => 
    m.question?.toLowerCase().includes(search.toLowerCase()) || 
    m.tags?.some(t => t.toLowerCase().includes(search.toLowerCase()))
  ) || [];

  return (
    <motion.div 
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      className="space-y-4"
    >
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4 mb-6">
        <div>
          <h2 className="text-2xl font-bold tracking-tight">Monitored Markets</h2>
          <p className="text-sm text-muted-foreground font-mono mt-1">
            Tracking {data?.count || 0} active prediction markets
          </p>
        </div>
        
        <div className="flex items-center gap-2 w-full sm:w-auto">
          <div className="relative w-full sm:w-64">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" size={16} />
            <input 
              type="text" 
              placeholder="Search markets..." 
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="w-full bg-card border border-border rounded-md py-2 pl-9 pr-4 text-sm font-mono focus:outline-none focus:border-primary focus:ring-1 focus:ring-primary transition-all"
            />
          </div>
          <button className="bg-card border border-border p-2 rounded-md hover:bg-accent text-muted-foreground hover:text-foreground transition-colors">
            <Filter size={18} />
          </button>
        </div>
      </div>

      <div className="terminal-panel">
        <div className="overflow-x-auto">
          <table className="w-full text-sm text-left">
            <thead className="text-xs font-mono text-muted-foreground uppercase bg-muted/30 border-b border-border">
              <tr>
                <th className="px-4 py-3 font-medium">Market</th>
                <th className="px-4 py-3 font-medium">Tags</th>
                <th className="px-4 py-3 font-medium text-right cursor-pointer hover:text-foreground group flex items-center justify-end gap-1">
                  Volume 24h <ArrowUpDown size={12} className="opacity-50 group-hover:opacity-100" />
                </th>
                <th className="px-4 py-3 font-medium text-right">Liquidity</th>
                <th className="px-4 py-3 font-medium text-center">Score</th>
                <th className="px-4 py-3 font-medium text-right">Expires</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border/50">
              {isLoading ? (
                <tr><td colSpan={6} className="p-4"><TableSkeleton /></td></tr>
              ) : filteredMarkets.length === 0 ? (
                <tr>
                  <td colSpan={6} className="px-4 py-8 text-center text-muted-foreground font-mono">
                    No markets match your criteria.
                  </td>
                </tr>
              ) : (
                filteredMarkets.map((market) => (
                  <tr 
                    key={market.marketId} 
                    onClick={() => setLocation(`/markets/${market.marketId}`)}
                    className="data-row cursor-pointer group"
                  >
                    <td className="px-4 py-4 font-medium text-foreground max-w-md truncate group-hover:text-primary transition-colors">
                      {market.question}
                      {market.negRisk && (
                        <Badge className="ml-2 text-purple-400 bg-purple-400/10 border-purple-400/20">Neg Risk</Badge>
                      )}
                    </td>
                    <td className="px-4 py-4">
                      <div className="flex gap-1 flex-wrap">
                        {market.tags?.slice(0, 2).map(tag => (
                          <span key={tag} className="text-[10px] uppercase font-mono px-1.5 py-0.5 rounded bg-muted text-muted-foreground border border-border">
                            {tag}
                          </span>
                        ))}
                        {(market.tags?.length || 0) > 2 && (
                          <span className="text-[10px] font-mono px-1 py-0.5 text-muted-foreground">+{market.tags!.length - 2}</span>
                        )}
                      </div>
                    </td>
                    <td className="px-4 py-4 text-right font-mono text-foreground">
                      {formatCurrency(market.volume24h)}
                    </td>
                    <td className="px-4 py-4 text-right font-mono text-muted-foreground">
                      {formatCurrency(market.liquidity)}
                    </td>
                    <td className="px-4 py-4 text-center">
                      <div className="inline-flex items-center justify-center bg-accent border border-border rounded-md px-2 py-1 min-w-[3rem] font-mono text-primary">
                        {parseNumeric(market.score).toFixed(1)}
                      </div>
                    </td>
                    <td className="px-4 py-4 text-right font-mono text-xs text-muted-foreground">
                      {market.endDate ? formatInTz(market.endDate, "MMM d", timezone) : 'N/A'}
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
