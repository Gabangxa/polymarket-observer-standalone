import {
  pgTable,
  text,
  boolean,
  numeric,
  timestamp,
} from "drizzle-orm/pg-core";
import { createInsertSchema, createSelectSchema } from "drizzle-zod";
import { z } from "zod/v4";

export const marketsTable = pgTable("markets", {
  marketId: text("market_id").primaryKey(),
  conditionId: text("condition_id"),
  question: text("question"),
  eventTitle: text("event_title"),
  eventSlug: text("event_slug"),
  tags: text("tags").array(),
  negRisk: boolean("neg_risk").default(false),
  tokenIds: text("token_ids").array(),
  outcomes: text("outcomes").array(),
  volume24h: numeric("volume_24h"),
  liquidity: numeric("liquidity"),
  endDate: timestamp("end_date", { withTimezone: true }),
  hoursToClose: numeric("hours_to_close"),
  feesEnabled: boolean("fees_enabled").default(false),
  category: text("category"),
  score: numeric("score"),
  addedAt: timestamp("added_at", { withTimezone: true }).defaultNow(),
  updatedAt: timestamp("updated_at", { withTimezone: true }).defaultNow(),
});

export const insertMarketSchema = createInsertSchema(marketsTable);
export const selectMarketSchema = createSelectSchema(marketsTable);
export type InsertMarket = z.infer<typeof insertMarketSchema>;
export type Market = typeof marketsTable.$inferSelect;
