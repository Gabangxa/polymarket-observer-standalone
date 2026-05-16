import { Router, type IRouter } from "express";
import { db } from "@workspace/db";
import { signalsTable, marketsTable, type InsertSignal } from "@workspace/db/schema";
import { desc, eq, gt, and, sql } from "drizzle-orm";

const router: IRouter = Router();

router.get("/signals/counts", async (req, res) => {
  try {
    const rows = await db
      .select({
        strategy: signalsTable.strategy,
        count: sql<number>`cast(count(*) as int)`,
      })
      .from(signalsTable)
      .where(gt(signalsTable.emittedAt, sql`NOW() - INTERVAL '24 hours'`))
      .groupBy(signalsTable.strategy);

    const counts: Record<string, number> = {};
    for (const row of rows) {
      counts[row.strategy] = row.count;
    }

    res.json({ counts });
  } catch (err) {
    req.log.error({ err }, "Failed to get signal counts");
    res.status(500).json({ error: "Failed to get signal counts" });
  }
});

router.get("/signals", async (req, res) => {
  try {
    const strategy = req.query.strategy as string | undefined;
    const marketId = req.query.marketId as string | undefined;
    const hours = Math.min(Number(req.query.hours) || 24, 336);
    const limit = Math.min(Number(req.query.limit) || 100, 500);
    const resolvedParam = req.query.resolved as string | undefined;

    const cutoff = sql`NOW() - ${hours} * INTERVAL '1 hour'`;

    const conditions = [gt(signalsTable.emittedAt, cutoff)];
    if (strategy) {
      conditions.push(eq(signalsTable.strategy, strategy));
    }
    if (marketId) {
      conditions.push(eq(signalsTable.marketId, marketId));
    }
    if (resolvedParam !== undefined) {
      conditions.push(eq(signalsTable.resolved, resolvedParam === "true"));
    }

    const signals = await db
      .select({
        id: signalsTable.id,
        strategy: signalsTable.strategy,
        marketId: signalsTable.marketId,
        eventSlug: signalsTable.eventSlug,
        signalScore: signalsTable.signalScore,
        metadata: signalsTable.metadata,
        emittedAt: signalsTable.emittedAt,
        entryPrice: signalsTable.entryPrice,
        exitPrice: signalsTable.exitPrice,
        pnl: signalsTable.pnl,
        resolved: signalsTable.resolved,
        outcome: signalsTable.outcome,
        question: marketsTable.question,
      })
      .from(signalsTable)
      .leftJoin(marketsTable, eq(signalsTable.marketId, marketsTable.marketId))
      .where(and(...conditions))
      .orderBy(desc(signalsTable.emittedAt))
      .limit(limit);

    res.json({ signals, count: signals.length });
  } catch (err) {
    req.log.error({ err }, "Failed to list signals");
    res.status(500).json({ error: "Failed to list signals" });
  }
});

router.post("/signals", async (req, res) => {
  try {
    const body = req.body as InsertSignal;
    if (!body.strategy) {
      res.status(400).json({ error: "strategy is required" });
      return;
    }
    if (body.signalScore != null && parseFloat(String(body.signalScore)) < 0.75) {
      res.status(400).json({ error: "signalScore must be ≥ 0.75" });
      return;
    }

    const { strategy, marketId, eventSlug } = body;

    const existing = await db
      .select({ id: signalsTable.id })
      .from(signalsTable)
      .where(
        and(
          eq(signalsTable.strategy, strategy),
          gt(signalsTable.emittedAt, sql`NOW() - INTERVAL '1 hour'`),
          marketId
            ? eq(signalsTable.marketId, marketId)
            : eventSlug
            ? eq(signalsTable.eventSlug, eventSlug)
            : sql`TRUE`,
        ),
      )
      .limit(1);

    if (existing.length > 0) {
      res.status(201).json({ id: -1 });
      return;
    }

    const [created] = await db
      .insert(signalsTable)
      .values(body)
      .returning({ id: signalsTable.id });

    res.status(201).json({ id: created.id });
  } catch (err) {
    req.log.error({ err }, "Failed to create signal");
    res.status(500).json({ error: "Failed to create signal" });
  }
});

export default router;
