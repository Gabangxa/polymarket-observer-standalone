import { Router, type IRouter } from "express";
import { db } from "@workspace/db";
import { botConfigTable } from "@workspace/db/schema";
import { eq, sql } from "drizzle-orm";

const router: IRouter = Router();

const BANKROLL_KEY       = "bankroll_usdc";
const MAX_POSITION_KEY   = "max_position_pct";
const MAX_PORTFOLIO_KEY  = "max_portfolio_pct";
const DEFAULT_POSITION_PCT  = 0.10;
const DEFAULT_PORTFOLIO_PCT = 0.33;

async function ensureTable() {
  await db.execute(sql`
    CREATE TABLE IF NOT EXISTS bot_config (
      key        TEXT PRIMARY KEY,
      value      TEXT NOT NULL,
      updated_at TIMESTAMPTZ DEFAULT NOW()
    )
  `);
}

router.get("/config/bankroll", async (req, res) => {
  try {
    await ensureTable();
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

    await ensureTable();
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

router.get("/config/risk", async (req, res) => {
  try {
    await ensureTable();
    const rows = await db
      .select({ key: botConfigTable.key, value: botConfigTable.value })
      .from(botConfigTable)
      .where(sql`${botConfigTable.key} IN (${MAX_POSITION_KEY}, ${MAX_PORTFOLIO_KEY})`);

    const map = Object.fromEntries(rows.map((r) => [r.key, parseFloat(r.value)]));
    res.json({
      positionPct:  map[MAX_POSITION_KEY]  ?? DEFAULT_POSITION_PCT,
      portfolioPct: map[MAX_PORTFOLIO_KEY] ?? DEFAULT_PORTFOLIO_PCT,
    });
  } catch (err) {
    req.log.error({ err }, "Failed to get risk config");
    res.status(500).json({ error: "Failed to get risk config" });
  }
});

router.put("/config/position-pct", async (req, res) => {
  try {
    const val = parseFloat(req.body?.pct);
    if (isNaN(val) || val <= 0 || val > 1) {
      res.status(400).json({ error: "pct must be between 0 and 1 (e.g. 0.25 for 25%)" });
      return;
    }
    await ensureTable();
    await db
      .insert(botConfigTable)
      .values({ key: MAX_POSITION_KEY, value: String(val) })
      .onConflictDoUpdate({
        target: botConfigTable.key,
        set: { value: String(val), updatedAt: new Date() },
      });
    res.json({ positionPct: val });
  } catch (err) {
    req.log.error({ err }, "Failed to set position pct");
    res.status(500).json({ error: "Failed to set position pct" });
  }
});

router.put("/config/portfolio-pct", async (req, res) => {
  try {
    const val = parseFloat(req.body?.pct);
    if (isNaN(val) || val <= 0 || val > 1) {
      res.status(400).json({ error: "pct must be between 0 and 1 (e.g. 0.80 for 80%)" });
      return;
    }
    await ensureTable();
    await db
      .insert(botConfigTable)
      .values({ key: MAX_PORTFOLIO_KEY, value: String(val) })
      .onConflictDoUpdate({
        target: botConfigTable.key,
        set: { value: String(val), updatedAt: new Date() },
      });
    res.json({ portfolioPct: val });
  } catch (err) {
    req.log.error({ err }, "Failed to set portfolio pct");
    res.status(500).json({ error: "Failed to set portfolio pct" });
  }
});

export default router;
