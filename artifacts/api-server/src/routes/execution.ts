import { Router, type IRouter } from "express";
import { db } from "@workspace/db";
import { botConfigTable } from "@workspace/db/schema";
import { eq, sql } from "drizzle-orm";

const router: IRouter = Router();

const PAUSED_KEY            = "executor_paused";
const CONNECTION_STATUS_KEY = "connection_status";

async function ensureTable() {
  await db.execute(sql`
    CREATE TABLE IF NOT EXISTS bot_config (
      key        TEXT PRIMARY KEY,
      value      TEXT NOT NULL,
      updated_at TIMESTAMPTZ DEFAULT NOW()
    )
  `);
}

async function setPausedFlag(paused: boolean) {
  await ensureTable();
  await db
    .insert(botConfigTable)
    .values({ key: PAUSED_KEY, value: paused ? "true" : "false" })
    .onConflictDoUpdate({
      target: botConfigTable.key,
      set: { value: paused ? "true" : "false", updatedAt: new Date() },
    });
}

router.get("/execution/status", async (req, res) => {
  try {
    await ensureTable();
    const rows = await db
      .select({ key: botConfigTable.key, value: botConfigTable.value })
      .from(botConfigTable)
      .where(sql`${botConfigTable.key} IN (${PAUSED_KEY}, ${CONNECTION_STATUS_KEY})`);

    const byKey = Object.fromEntries(rows.map((r) => [r.key, r.value]));

    let connection: {
      clobReachable: boolean;
      walletAuthenticated: boolean;
      checkedAt: string;
      error?: string | null;
    } | null = null;

    if (byKey[CONNECTION_STATUS_KEY]) {
      try {
        connection = JSON.parse(byKey[CONNECTION_STATUS_KEY]);
      } catch {
        // malformed JSON — surface nothing
      }
    }

    res.json({
      paused:     byKey[PAUSED_KEY] === "true",
      connection,
    });
  } catch (err) {
    req.log.error({ err }, "Failed to get executor status");
    res.status(500).json({ error: "Failed to get executor status" });
  }
});

router.post("/execution/pause", async (req, res) => {
  try {
    await setPausedFlag(true);
    res.json({ paused: true });
  } catch (err) {
    req.log.error({ err }, "Failed to pause executor");
    res.status(500).json({ error: "Failed to pause executor" });
  }
});

router.post("/execution/resume", async (req, res) => {
  try {
    await setPausedFlag(false);
    res.json({ paused: false });
  } catch (err) {
    req.log.error({ err }, "Failed to resume executor");
    res.status(500).json({ error: "Failed to resume executor" });
  }
});

export default router;
