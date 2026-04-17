import { Router, type IRouter } from "express";
import { db } from "@workspace/db";
import { snapshotsTable, marketsTable, type InsertSnapshot } from "@workspace/db/schema";
import { desc, eq } from "drizzle-orm";

const router: IRouter = Router();

router.get("/snapshots", async (req, res) => {
  try {
    const limit = Math.min(Number(req.query.limit) || 100, 500);

    const snapshots = await db
      .selectDistinctOn([snapshotsTable.marketId], {
        id: snapshotsTable.id,
        marketId: snapshotsTable.marketId,
        collectedAt: snapshotsTable.collectedAt,
        yesPrice: snapshotsTable.yesPrice,
        noPrice: snapshotsTable.noPrice,
        spread: snapshotsTable.spread,
        midpoint: snapshotsTable.midpoint,
        feeRateBps: snapshotsTable.feeRateBps,
        openInterest: snapshotsTable.openInterest,
        priceHistory: snapshotsTable.priceHistory,
        topHolders: snapshotsTable.topHolders,
        recentTrades: snapshotsTable.recentTrades,
        errors: snapshotsTable.errors,
        yesAsk: snapshotsTable.yesAsk,
        noAsk: snapshotsTable.noAsk,
        question: marketsTable.question,
        eventSlug: marketsTable.eventSlug,
        tags: marketsTable.tags,
        negRisk: marketsTable.negRisk,
      })
      .from(snapshotsTable)
      .innerJoin(marketsTable, eq(snapshotsTable.marketId, marketsTable.marketId))
      .orderBy(snapshotsTable.marketId, desc(snapshotsTable.collectedAt))
      .limit(limit);

    res.json({ snapshots, count: snapshots.length });
  } catch (err) {
    req.log.error({ err }, "Failed to list snapshots");
    res.status(500).json({ error: "Failed to list snapshots" });
  }
});

router.post("/snapshots", async (req, res) => {
  try {
    const body = req.body as InsertSnapshot;
    if (!body.marketId) {
      res.status(400).json({ error: "marketId is required" });
      return;
    }

    const [created] = await db
      .insert(snapshotsTable)
      .values(body)
      .returning({ id: snapshotsTable.id });

    res.status(201).json({ id: created.id });
  } catch (err) {
    req.log.error({ err }, "Failed to create snapshot");
    res.status(500).json({ error: "Failed to create snapshot" });
  }
});

router.get("/snapshots/:marketId", async (req, res) => {
  try {
    const { marketId } = req.params;
    const limit = Math.min(Number(req.query.limit) || 336, 1000);

    const snapshots = await db
      .select({
        id: snapshotsTable.id,
        marketId: snapshotsTable.marketId,
        collectedAt: snapshotsTable.collectedAt,
        yesPrice: snapshotsTable.yesPrice,
        noPrice: snapshotsTable.noPrice,
        spread: snapshotsTable.spread,
        midpoint: snapshotsTable.midpoint,
        feeRateBps: snapshotsTable.feeRateBps,
        openInterest: snapshotsTable.openInterest,
        priceHistory: snapshotsTable.priceHistory,
        topHolders: snapshotsTable.topHolders,
        recentTrades: snapshotsTable.recentTrades,
        errors: snapshotsTable.errors,
        yesAsk: snapshotsTable.yesAsk,
        noAsk: snapshotsTable.noAsk,
        question: marketsTable.question,
        tags: marketsTable.tags,
        negRisk: marketsTable.negRisk,
      })
      .from(snapshotsTable)
      .innerJoin(marketsTable, eq(snapshotsTable.marketId, marketsTable.marketId))
      .where(eq(snapshotsTable.marketId, marketId))
      .orderBy(desc(snapshotsTable.collectedAt))
      .limit(limit);

    res.json({ snapshots, count: snapshots.length });
  } catch (err) {
    req.log.error({ err }, "Failed to get market snapshots");
    res.status(500).json({ error: "Failed to get market snapshots" });
  }
});

export default router;
