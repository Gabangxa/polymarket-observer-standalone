# agents/pnl_tracker.py
# Recomputes unrealized PnL (pnl_open) for every open position each pipeline cycle.
#
# Unrealized PnL — mark-to-market, changes with every price move:
#   pnl_open = (current_price - avg_cost) × net_shares
#   net_shares = total_bought - total_sold
#
# Realized PnL — locked when a position exits, never changes after that:
#   pnl_realized += (exit_price - avg_cost) × shares_exited
#   exit_price = 1.0 (YES resolution) | 0.0 (NO resolution) | fill_price (sell order)
#   Written by hindsight_logger on market resolution.

import logging
import db

logger = logging.getLogger(__name__)


def run() -> dict:
    logger.info("=== PnL tracker starting ===")

    positions = db.get_all_open_positions()
    if not positions:
        logger.info("No open positions to mark")
        return {"agent": "pnl_tracker", "updated": 0, "skipped": 0}

    market_ids = list({p["market_id"] for p in positions if p.get("market_id")})
    snapshots  = db.get_latest_snapshots_for_markets(market_ids)
    price_by_market = {s["market_id"]: s for s in snapshots}

    updated = 0
    skipped = 0

    for pos in positions:
        market_id  = pos["market_id"]
        avg_cost   = float(pos.get("avg_cost") or 0)
        net_shares = float(pos.get("total_bought") or 0) - float(pos.get("total_sold") or 0)

        if net_shares <= 0 or avg_cost == 0:
            # No open exposure, or no cost basis yet (order not yet filled)
            skipped += 1
            continue

        snap = price_by_market.get(market_id)
        if not snap:
            skipped += 1
            continue

        side = pos.get("side", "YES")
        raw_price = snap.get("yes_price") if side == "YES" else snap.get("no_price")
        if raw_price is None:
            skipped += 1
            continue

        current_price = float(raw_price)
        pnl_open = (current_price - avg_cost) * net_shares

        db.update_position_pnl_open(
            market_id, pos["token_id"], side,
            round(pnl_open, 6),
        )
        updated += 1
        logger.debug(
            f"  {market_id[:12]}… | {side} | net={net_shares:.2f} "
            f"avg_cost={avg_cost:.4f} price={current_price:.4f} "
            f"pnl_open={pnl_open:+.6f}"
        )

    logger.info(f"PnL tracker — updated: {updated}, skipped: {skipped}")
    return {"agent": "pnl_tracker", "updated": updated, "skipped": skipped}
