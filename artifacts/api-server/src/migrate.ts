import { db, pool } from "@workspace/db";
import { sql } from "drizzle-orm";

async function migrate() {
  await db.execute(sql`ALTER TABLE snapshots ADD COLUMN IF NOT EXISTS yes_ask NUMERIC`);
  await db.execute(sql`ALTER TABLE snapshots ADD COLUMN IF NOT EXISTS no_ask NUMERIC`);
  console.log("Migration complete: yes_ask, no_ask columns ensured");
  await pool.end();
}

migrate().catch((err) => {
  console.error("Migration failed:", err);
  process.exit(1);
});
