import { useQuery } from "@tanstack/react-query";
import {
  getHealthCheckQueryOptions,
  getListMarketsQueryOptions,
  getListSnapshotsQueryOptions,
  getGetMarketSnapshotsQueryOptions,
  getListSignalsQueryOptions,
  getGetSignalCountsQueryOptions,
} from "@workspace/api-client-react";
import type { ListSnapshotsParams, GetMarketSnapshotsParams, ListSignalsParams } from "@workspace/api-client-react";

const POLLING_INTERVAL = 30000;

export function useLiveHealth() {
  return useQuery({
    ...getHealthCheckQueryOptions(),
    refetchInterval: POLLING_INTERVAL,
  });
}

export function useLiveMarkets() {
  return useQuery({
    ...getListMarketsQueryOptions(),
    refetchInterval: POLLING_INTERVAL,
  });
}

// `executableOnly` is an opt-in server filter (score >= threshold AND strategy
// in the execution allowlist) that the generated client type doesn't know
// about. The generated URL builder forwards any param key, so we widen the
// type and cast at the call boundary rather than regenerating the client.
export function useLiveSnapshots(
  params?: ListSnapshotsParams & { executableOnly?: boolean },
) {
  return useQuery({
    ...getListSnapshotsQueryOptions(params as ListSnapshotsParams),
    refetchInterval: POLLING_INTERVAL,
  });
}

export function useLiveMarketHistory(marketId: string, params?: GetMarketSnapshotsParams) {
  return useQuery({
    ...getGetMarketSnapshotsQueryOptions(marketId, params),
    refetchInterval: POLLING_INTERVAL,
    enabled: !!marketId,
  });
}

export function useLiveSignals(
  params?: ListSignalsParams & { executableOnly?: boolean },
) {
  return useQuery({
    ...getListSignalsQueryOptions(params as ListSignalsParams),
    refetchInterval: POLLING_INTERVAL,
  });
}

export function useLiveSignalCounts() {
  return useQuery({
    ...getGetSignalCountsQueryOptions(),
    refetchInterval: POLLING_INTERVAL,
  });
}

// ── Types for performance endpoint (not in generated client) ─────────────────

export interface StrategyPerf {
  strategy:      string;
  signalCount:   number;
  resolvedCount: number;
  winCount:      number;
  winRate:       number | null;
  avgPnl:        number | null;
}

export interface CategoryPerf {
  category:      string;
  strategy:      string;
  signalCount:   number;
  resolvedCount: number;
  winCount:      number;
  winRate:       number | null;
  avgPnl:        number | null;
}

export interface PerformanceData {
  strategies: StrategyPerf[];
  categories: CategoryPerf[];
}

export function useStrategyPerformance() {
  return useQuery<PerformanceData>({
    queryKey: ["strategies", "performance"],
    queryFn: async () => {
      const res = await fetch("/api/strategies/performance");
      if (!res.ok) throw new Error("Failed to fetch performance data");
      return res.json();
    },
    refetchInterval: POLLING_INTERVAL,
  });
}

export function useMarketSignals(marketId: string) {
  return useQuery({
    queryKey: ["signals", "market", marketId],
    queryFn: async () => {
      const res = await fetch(
        `/api/signals?marketId=${encodeURIComponent(marketId)}&hours=168&limit=5`,
      );
      if (!res.ok) throw new Error("Failed to fetch market signals");
      return res.json() as Promise<{ signals: any[]; count: number }>;
    },
    refetchInterval: POLLING_INTERVAL,
    enabled: !!marketId,
  });
}
