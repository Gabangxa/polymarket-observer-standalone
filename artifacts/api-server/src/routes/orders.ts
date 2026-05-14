import { Router, type IRouter } from "express";
import { db } from "@workspace/db";
import { ordersTable, positionsTable, marketsTable, botConfigTable } from "@workspace/db/schema";
import { desc, eq, inArray, sql } from "drizzle-orm";

const router: IRouter = Router();

const ACTIVE_STATUSES = [
  "PENDING_SUBMISSION",
  "SUBMITTED",
  "PARTIALLY_FILLED",
  "CANCEL_REQUESTED",
];
const CLOSED_STATUSES = ["FILLED", "CANCELED", "REJECTED", "EXPIRED", "ERROR"];

router.get("/orders", async (req, res) => {
  try {
    const statusGroup = (req.query.status as string) ?? "active";
    const limit = Math.min(Number(req.query.limit) || 200, 500);

    const statuses =
      statusGroup === "closed"
        ? CLOSED_STATUSES
        : statusGroup === "all"
          ? [...ACTIVE_STATUSES, ...CLOSED_STATUSES]
          : ACTIVE_STATUSES;

    const orders = await db
      .select({
        id: ordersTable.id,
        clordId: ordersTable.clordId,
        signalId: ordersTable.signalId,
        marketId: ordersTable.marketId,
        tokenId: ordersTable.tokenId,
        side: ordersTable.side,
        price: ordersTable.price,
        sizeUsdc: ordersTable.sizeUsdc,
        strategy: ordersTable.strategy,
        status: ordersTable.status,
        exchangeOrderId: ordersTable.exchangeOrderId,
        workingQty: ordersTable.workingQty,
        filledQty: ordersTable.filledQty,
        fillPrice: ordersTable.fillPrice,
        submittedAt: ordersTable.submittedAt,
        filledAt: ordersTable.filledAt,
        canceledAt: ordersTable.canceledAt,
        errorMsg: ordersTable.errorMsg,
        createdAt: ordersTable.createdAt,
        question: marketsTable.question,
      })
      .from(ordersTable)
      .leftJoin(marketsTable, eq(ordersTable.marketId, marketsTable.marketId))
      .where(inArray(ordersTable.status, statuses))
      .orderBy(desc(ordersTable.createdAt))
      .limit(limit);

    res.json({ orders, count: orders.length });
  } catch (err) {
    req.log.error({ err }, "Failed to list orders");
    res.status(500).json({ error: "Failed to list orders" });
  }
});

router.delete("/orders/:id", async (req, res) => {
  try {
    const id = Number(req.params.id);
    if (!id || isNaN(id)) {
      res.status(400).json({ error: "Invalid order id" });
      return;
    }

    const [updated] = await db
      .update(ordersTable)
      .set({ status: "CANCEL_REQUESTED" })
      .where(
        sql`${ordersTable.id} = ${id} AND ${ordersTable.status} IN (${sql.join(
          ACTIVE_STATUSES.filter((s) => s !== "CANCEL_REQUESTED").map(
            (s) => sql`${s}`,
          ),
          sql`, `,
        )})`,
      )
      .returning({ id: ordersTable.id, status: ordersTable.status });

    if (!updated) {
      res.status(404).json({ error: "Order not found or already closed" });
      return;
    }

    res.json({ id: updated.id, status: updated.status });
  } catch (err) {
    req.log.error({ err }, "Failed to cancel order");
    res.status(500).json({ error: "Failed to cancel order" });
  }
});

router.post("/orders/cancel-all", async (req, res) => {
  try {
    const cancelable = ACTIVE_STATUSES.filter((s) => s !== "CANCEL_REQUESTED");

    const updated = await db
      .update(ordersTable)
      .set({ status: "CANCEL_REQUESTED" })
      .where(inArray(ordersTable.status, cancelable))
      .returning({ id: ordersTable.id });

    res.json({ canceled: updated.length });
  } catch (err) {
    req.log.error({ err }, "Failed to cancel all orders");
    res.status(500).json({ error: "Failed to cancel all orders" });
  }
});

router.get("/positions", async (req, res) => {
  try {
    const positions = await db
      .select({
        id: positionsTable.id,
        marketId: positionsTable.marketId,
        tokenId: positionsTable.tokenId,
        side: positionsTable.side,
        totalBought: positionsTable.totalBought,
        totalSold: positionsTable.totalSold,
        workingBuy: positionsTable.workingBuy,
        workingSell: positionsTable.workingSell,
        avgCost: positionsTable.avgCost,
        pnlRealized: positionsTable.pnlRealized,
        pnlOpen: positionsTable.pnlOpen,
        lastUpdated: positionsTable.lastUpdated,
        question: marketsTable.question,
      })
      .from(positionsTable)
      .leftJoin(marketsTable, eq(positionsTable.marketId, marketsTable.marketId))
      .where(
        sql`(${positionsTable.totalBought}::numeric - ${positionsTable.totalSold}::numeric) <> 0
          OR ${positionsTable.workingBuy}::numeric > 0
          OR ${positionsTable.workingSell}::numeric > 0`,
      )
      .orderBy(desc(positionsTable.lastUpdated));

    res.json({ positions, count: positions.length });
  } catch (err) {
    req.log.error({ err }, "Failed to list positions");
    res.status(500).json({ error: "Failed to list positions" });
  }
});

router.get("/portfolio", async (req, res) => {
  try {
    const [configRow] = await db
      .select({ value: botConfigTable.value })
      .from(botConfigTable)
      .where(eq(botConfigTable.key, "bankroll_usdc"))
      .limit(1);
    const bankroll = configRow
      ? parseFloat(configRow.value) || 0
      : parseFloat(process.env.BANKROLL_USDC ?? "0") || 0;

    const [riskRow] = await db
      .select({ total: sql<string>`COALESCE(SUM(${ordersTable.sizeUsdc}::numeric), 0)` })
      .from(ordersTable)
      .where(inArray(ordersTable.status, ACTIVE_STATUSES));

    const [pnlRow] = await db
      .select({
        realized: sql<string>`COALESCE(SUM(${positionsTable.pnlRealized}::numeric), 0)`,
        open: sql<string>`COALESCE(SUM(${positionsTable.pnlOpen}::numeric), 0)`,
      })
      .from(positionsTable);

    const atRisk = parseFloat(riskRow?.total ?? "0");
    const available = Math.max(bankroll - atRisk, 0);
    const deployedPct = bankroll > 0 ? atRisk / bankroll : 0;
    const pnlRealized = parseFloat(pnlRow?.realized ?? "0");
    const pnlOpen = parseFloat(pnlRow?.open ?? "0");

    res.json({ bankroll, atRisk, available, deployedPct, pnlRealized, pnlOpen });
  } catch (err) {
    req.log.error({ err }, "Failed to get portfolio");
    res.status(500).json({ error: "Failed to get portfolio" });
  }
});

export default router;
