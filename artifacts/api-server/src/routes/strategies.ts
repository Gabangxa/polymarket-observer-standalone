import { Router, type IRouter } from "express";
import { db } from "@workspace/db";
import { signalsTable, marketsTable } from "@workspace/db/schema";
import { sql, eq, gte } from "drizzle-orm";

const router: IRouter = Router();

router.get("/strategies/performance", async (req, res) => {
  try {
    // ── Per-strategy stats ────────────────────────────────────────────────────
    const startOfToday = new Date();
    startOfToday.setUTCHours(0, 0, 0, 0);

    const strategyRows = await db
      .select({
        strategy:      signalsTable.strategy,
        signalCount:   sql<number>`cast(count(*) as int)`,
        resolvedCount: sql<number>`cast(sum(case when ${signalsTable.resolved} then 1 else 0 end) as int)`,
        winCount:      sql<number>`cast(sum(case when ${signalsTable.outcome} = true then 1 else 0 end) as int)`,
        avgPnl:        sql<number>`round(avg(case when ${signalsTable.resolved} then cast(${signalsTable.pnl} as numeric) end)::numeric, 4)`,
      })
      .from(signalsTable)
      .where(gte(signalsTable.emittedAt, startOfToday))
      .groupBy(signalsTable.strategy);

    const strategies = strategyRows.map((r) => ({
      strategy:      r.strategy,
      signalCount:   r.signalCount,
      resolvedCount: r.resolvedCount,
      winCount:      r.winCount ?? 0,
      winRate:
        r.resolvedCount > 0
          ? Math.round(((r.winCount ?? 0) / r.resolvedCount) * 10000) / 10000
          : null,
      avgPnl: r.avgPnl ?? null,
    }));

    // ── Per-category (first tag) × strategy stats ─────────────────────────────
    const categoryRows = await db
      .select({
        category:      sql<string>`coalesce((${marketsTable.tags})[1], 'uncategorized')`,
        strategy:      signalsTable.strategy,
        signalCount:   sql<number>`cast(count(*) as int)`,
        resolvedCount: sql<number>`cast(sum(case when ${signalsTable.resolved} then 1 else 0 end) as int)`,
        winCount:      sql<number>`cast(sum(case when ${signalsTable.outcome} = true then 1 else 0 end) as int)`,
        avgPnl:        sql<number>`round(avg(case when ${signalsTable.resolved} then cast(${signalsTable.pnl} as numeric) end)::numeric, 4)`,
      })
      .from(signalsTable)
      .leftJoin(marketsTable, eq(signalsTable.marketId, marketsTable.marketId))
      .where(gte(signalsTable.emittedAt, startOfToday))
      .groupBy(
        sql`coalesce((${marketsTable.tags})[1], 'uncategorized')`,
        signalsTable.strategy,
      );

    const categories = categoryRows.map((r) => ({
      category:      r.category,
      strategy:      r.strategy,
      signalCount:   r.signalCount,
      resolvedCount: r.resolvedCount,
      winCount:      r.winCount ?? 0,
      winRate:
        r.resolvedCount > 0
          ? Math.round(((r.winCount ?? 0) / r.resolvedCount) * 10000) / 10000
          : null,
      avgPnl: r.avgPnl ?? null,
    }));

    res.json({ strategies, categories });
  } catch (err) {
    req.log.error({ err }, "Failed to get strategy performance");
    res.status(500).json({ error: "Failed to get strategy performance" });
  }
});

export default router;
