import { Router, type IRouter } from "express";
import { db } from "@workspace/db";
import { snapshotsTable, marketsTable, type InsertSnapshot } from "@workspace/db/schema";
import { desc, eq, sql } from "drizzle-orm";

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
    const hoursRaw = req.query.hours ? Number(req.query.hours) : null;

    if (hoursRaw !== null && !isNaN(hoursRaw) && hoursRaw > 0) {
      // Return one snapshot per hour over the requested window.
      // DISTINCT ON date_trunc('hour') picks the most recent snapshot in each hour bucket,
      // keeping the response size bounded at ~hours rows regardless of collection frequency.
      const hours = Math.min(hoursRaw, 720);
      const result = await db.execute(sql`
        SELECT DISTINCT ON (date_trunc('hour', s.collected_at))
          s.id,
          s.market_id         AS "marketId",
          s.collected_at      AS "collectedAt",
          s.yes_price         AS "yesPrice",
          s.no_price          AS "noPrice",
          s.spread,
          s.midpoint,
          s.fee_rate_bps      AS "feeRateBps",
          s.open_interest     AS "openInterest",
          s.price_history     AS "priceHistory",
          s.top_holders       AS "topHolders",
          s.recent_trades     AS "recentTrades",
          s.errors,
          s.yes_ask           AS "yesAsk",
          s.no_ask            AS "noAsk",
          m.question,
          m.tags,
          m.neg_risk          AS "negRisk"
        FROM snapshots s
        INNER JOIN markets m ON m.market_id = s.market_id
        WHERE s.market_id = ${marketId}
          AND s.collected_at > now() - (${hours} * interval '1 hour')
        ORDER BY date_trunc('hour', s.collected_at) DESC, s.collected_at DESC
      `);
      const snapshots = result.rows;
      res.json({ snapshots, count: snapshots.length });
      return;
    }

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
