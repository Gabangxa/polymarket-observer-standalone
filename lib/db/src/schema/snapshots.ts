import {
  pgTable,
  text,
  numeric,
  timestamp,
  bigserial,
  index,
  jsonb,
} from "drizzle-orm/pg-core";
import { createInsertSchema, createSelectSchema } from "drizzle-zod";
import { z } from "zod/v4";

export const snapshotsTable = pgTable(
  "snapshots",
  {
    id: bigserial("id", { mode: "number" }).primaryKey(),
    marketId: text("market_id").notNull(),
    collectedAt: timestamp("collected_at", { withTimezone: true }).defaultNow(),
    yesPrice: numeric("yes_price"),
    noPrice: numeric("no_price"),
    spread: numeric("spread"),
    midpoint: numeric("midpoint"),
    feeRateBps: numeric("fee_rate_bps"),
    openInterest: numeric("open_interest"),
    priceHistory: jsonb("price_history"),
    topHolders: jsonb("top_holders"),
    recentTrades: jsonb("recent_trades"),
    errors: text("errors").array(),
    yesAsk: numeric("yes_ask"),
    noAsk: numeric("no_ask"),
  },
  (t) => [
    index("snapshots_market_collected").on(t.marketId, t.collectedAt),
  ],
);

export const insertSnapshotSchema = createInsertSchema(snapshotsTable).omit({ id: true });
export const selectSnapshotSchema = createSelectSchema(snapshotsTable);
export type InsertSnapshot = z.infer<typeof insertSnapshotSchema>;
export type Snapshot = typeof snapshotsTable.$inferSelect;
