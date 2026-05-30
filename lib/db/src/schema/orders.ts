import {
  pgTable,
  text,
  numeric,
  timestamp,
  bigserial,
  bigint,
  index,
} from "drizzle-orm/pg-core";
import { createSelectSchema } from "drizzle-zod";

export const ordersTable = pgTable(
  "orders",
  {
    id: bigserial("id", { mode: "number" }).primaryKey(),
    clordId: text("clord_id").notNull().unique(),
    signalId: bigint("signal_id", { mode: "number" }),
    marketId: text("market_id").notNull(),
    tokenId: text("token_id").notNull(),
    side: text("side").notNull(),
    price: numeric("price").notNull(),
    sizeUsdc: numeric("size_usdc").notNull(),
    strategy: text("strategy").notNull(),
    status: text("status").notNull().default("PENDING_SUBMISSION"),
    exchangeOrderId: text("exchange_order_id"),
    workingQty: numeric("working_qty"),
    filledQty: numeric("filled_qty"),
    fillPrice: numeric("fill_price"),
    submittedAt: timestamp("submitted_at", { withTimezone: true }),
    filledAt: timestamp("filled_at", { withTimezone: true }),
    canceledAt: timestamp("canceled_at", { withTimezone: true }),
    errorMsg: text("error_msg"),
    createdAt: timestamp("created_at", { withTimezone: true }).defaultNow(),
  },
  (t) => [
    index("orders_status_created").on(t.status, t.createdAt),
    index("orders_signal").on(t.signalId),
  ],
);

export const positionsTable = pgTable("positions", {
  id: bigserial("id", { mode: "number" }).primaryKey(),
  marketId: text("market_id").notNull(),
  tokenId: text("token_id").notNull(),
  side: text("side").notNull(),
  totalBought: numeric("total_bought"),
  totalSold: numeric("total_sold"),
  workingBuy: numeric("working_buy"),
  workingSell: numeric("working_sell"),
  avgCost: numeric("avg_cost"),
  pnlRealized: numeric("pnl_realized"),
  pnlOpen: numeric("pnl_open"),
  lastUpdated: timestamp("last_updated", { withTimezone: true }),
});

export const selectOrderSchema = createSelectSchema(ordersTable);
export const selectPositionSchema = createSelectSchema(positionsTable);
export type Order = typeof ordersTable.$inferSelect;
export type Position = typeof positionsTable.$inferSelect;
