import { useQuery } from "@tanstack/react-query";
import {
  getHealthCheckQueryOptions,
  getListMarketsQueryOptions,
  getListSnapshotsQueryOptions,
  getGetMarketSnapshotsQueryOptions,
  getListSignalsQueryOptions,
  getGetSignalCountsQueryOptions,
  getListOrdersQueryOptions,
  getListPositionsQueryOptions,
  getGetPortfolioQueryOptions,
  useCancelOrder,
  useCancelAllOrders,
} from "@workspace/api-client-react";
import type {
  ListSnapshotsParams,
  GetMarketSnapshotsParams,
  ListSignalsParams,
  ListOrdersParams,
} from "@workspace/api-client-react";

export { useCancelOrder, useCancelAllOrders };

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

export function useLiveSnapshots(params?: ListSnapshotsParams) {
  return useQuery({
    ...getListSnapshotsQueryOptions(params),
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

export function useLiveSignals(params?: ListSignalsParams) {
  return useQuery({
    ...getListSignalsQueryOptions(params),
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

export function useLiveOrders(params?: ListOrdersParams) {
  return useQuery({
    ...getListOrdersQueryOptions(params),
    refetchInterval: POLLING_INTERVAL,
  });
}

export function useLivePositions() {
  return useQuery({
    ...getListPositionsQueryOptions(),
    refetchInterval: POLLING_INTERVAL,
  });
}

export function useLivePortfolio() {
  return useQuery({
    ...getGetPortfolioQueryOptions(),
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
