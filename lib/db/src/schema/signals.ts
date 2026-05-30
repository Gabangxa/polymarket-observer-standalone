import {
  pgTable,
  text,
  numeric,
  timestamp,
  bigserial,
  boolean,
  index,
  jsonb,
} from "drizzle-orm/pg-core";
import { createInsertSchema, createSelectSchema } from "drizzle-zod";
import { z } from "zod/v4";

export const signalsTable = pgTable(
  "signals",
  {
    id: bigserial("id", { mode: "number" }).primaryKey(),
    strategy: text("strategy").notNull(),
    marketId: text("market_id"),
    eventSlug: text("event_slug"),
    signalScore: numeric("signal_score"),
    metadata: jsonb("metadata"),
    emittedAt: timestamp("emitted_at", { withTimezone: true }).defaultNow(),
    entryPrice: numeric("entry_price"),
    exitPrice: numeric("exit_price"),
    pnl: numeric("pnl"),
    resolved: boolean("resolved").default(false),
    outcome: boolean("outcome"),
    executed: boolean("executed").default(false),
    executedSkipReason: text("executed_skip_reason"),
  },
  (t) => [
    index("signals_strategy_emitted").on(t.strategy, t.emittedAt),
    index("signals_market_emitted").on(t.marketId, t.emittedAt),
  ],
);

export const insertSignalSchema = createInsertSchema(signalsTable).omit({ id: true });
export const selectSignalSchema = createSelectSchema(signalsTable);
export type InsertSignal = z.infer<typeof insertSignalSchema>;
export type Signal = typeof signalsTable.$inferSelect;
