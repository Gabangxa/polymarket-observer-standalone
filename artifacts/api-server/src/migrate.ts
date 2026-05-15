import { db, pool } from "@workspace/db";
import { sql } from "drizzle-orm";

async function migrate() {
  await db.execute(sql`ALTER TABLE snapshots ADD COLUMN IF NOT EXISTS yes_ask NUMERIC`);
  await db.execute(sql`ALTER TABLE snapshots ADD COLUMN IF NOT EXISTS no_ask NUMERIC`);
  await db.execute(sql`ALTER TABLE signals ADD COLUMN IF NOT EXISTS executed BOOLEAN DEFAULT FALSE`);
  await db.execute(sql`
    CREATE TABLE IF NOT EXISTS bot_config (
      key        TEXT PRIMARY KEY,
      value      TEXT NOT NULL,
      updated_at TIMESTAMPTZ DEFAULT NOW()
    )
  `);
  console.log("Migration complete");
  await pool.end();
}

migrate().catch((err) => {
  console.error("Migration failed:", err);
  process.exit(1);
});
