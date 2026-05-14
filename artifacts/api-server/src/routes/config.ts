import { Router, type IRouter } from "express";
import { db } from "@workspace/db";
import { botConfigTable } from "@workspace/db/schema";
import { eq } from "drizzle-orm";

const router: IRouter = Router();

const BANKROLL_KEY = "bankroll_usdc";

router.get("/config/bankroll", async (req, res) => {
  try {
    const [row] = await db
      .select({ value: botConfigTable.value })
      .from(botConfigTable)
      .where(eq(botConfigTable.key, BANKROLL_KEY))
      .limit(1);

    const bankroll = row ? parseFloat(row.value) || 0 : 0;
    res.json({ bankroll });
  } catch (err) {
    req.log.error({ err }, "Failed to get bankroll config");
    res.status(500).json({ error: "Failed to get bankroll config" });
  }
});

router.put("/config/bankroll", async (req, res) => {
  try {
    const raw = req.body?.bankroll;
    const bankroll = parseFloat(raw);
    if (isNaN(bankroll) || bankroll < 0) {
      res.status(400).json({ error: "bankroll must be a non-negative number" });
      return;
    }

    await db
      .insert(botConfigTable)
      .values({ key: BANKROLL_KEY, value: String(bankroll) })
      .onConflictDoUpdate({
        target: botConfigTable.key,
        set: { value: String(bankroll), updatedAt: new Date() },
      });

    res.json({ bankroll });
  } catch (err) {
    req.log.error({ err }, "Failed to set bankroll config");
    res.status(500).json({ error: "Failed to set bankroll config" });
  }
});

export default router;
