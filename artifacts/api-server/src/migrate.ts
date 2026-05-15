import { db, pool } from "@workspace/db";
import { sql } from "drizzle-orm";

async function run(stmt: string) {
  try {
    await db.execute(sql.raw(stmt));
    console.log(`OK: ${stmt.slice(0, 80)}`);
  } catch (err: any) {
    // Additive migrations are best-effort — log and continue.
    // The Python bot's _run_migrations() is the authoritative fallback.
    console.warn(`SKIP: ${stmt.slice(0, 80)} — ${err.message}`);
  }
}

async function migrate() {
  await run("ALTER TABLE snapshots ADD COLUMN IF NOT EXISTS yes_ask NUMERIC");
  await run("ALTER TABLE snapshots ADD COLUMN IF NOT EXISTS no_ask NUMERIC");
  await run("ALTER TABLE signals ADD COLUMN IF NOT EXISTS executed BOOLEAN DEFAULT FALSE");
  await run("ALTER TABLE orders ADD COLUMN IF NOT EXISTS expiration_ts TIMESTAMPTZ");
  await run("ALTER TABLE orders ADD COLUMN IF NOT EXISTS reprice_of BIGINT REFERENCES orders(id)");
  await run("ALTER TABLE orders ADD COLUMN IF NOT EXISTS repriced BOOLEAN DEFAULT FALSE");
  await run(`
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
