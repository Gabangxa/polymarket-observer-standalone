import logging

import db
from config import YIELD_MIN_PRICE, YIELD_HOURS_TO_EXPIRY

logger = logging.getLogger(__name__)


def _get_snapshots_with_hours_to_close(limit: int = 100) -> list[dict]:
    """Fetch latest snapshot per market including hours_to_close from the markets table."""
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT DISTINCT ON (s.market_id)
                    s.*,
                    m.question,
                    m.event_slug,
                    m.tags,
                    m.neg_risk,
                    m.hours_to_close
                FROM snapshots s
                JOIN markets m ON m.market_id = s.market_id
                ORDER BY s.market_id, s.collected_at DESC
                LIMIT %s
            """, (limit,))
            return [dict(r) for r in cur.fetchall()]


def run():
    logger.info("=== Tail yield engine starting ===")

    snapshots = _get_snapshots_with_hours_to_close()
    signals   = 0
    top       = None

    for snap in snapshots:
        yes_price      = snap.get("yes_price")
        hours_to_close = snap.get("hours_to_close")

        if yes_price is None or hours_to_close is None:
            continue
        try:
            yes_price      = float(yes_price)
            hours_to_close = float(hours_to_close)
        except (TypeError, ValueError):
            continue

        if not (YIELD_MIN_PRICE <= yes_price < 0.99):
            continue
        if hours_to_close > YIELD_HOURS_TO_EXPIRY:
            continue

        yield_pct = ((1.00 / yes_price) - 1) * 100
        score     = min(yield_pct / 5.0, 1.0)

        signal = {
            "strategy":        "tail_yield_engine",
            "market_id":       snap["market_id"],
            "event_slug":      snap.get("event_slug"),
            "signal_score":    round(score, 4),
            "hours_remaining": round(hours_to_close, 2),
            "current_price":   yes_price,
            "yield_percentage": round(yield_pct, 4),
            "trigger": (
                f"YES at {yes_price:.4f} with {hours_to_close:.1f}h to close — "
                f"yield {yield_pct:.2f}% "
                f"(threshold: price ≥ {YIELD_MIN_PRICE}, ≤ {YIELD_HOURS_TO_EXPIRY}h to expiry)."
            ),
        }

        sid = db.insert_signal(signal)
        if sid != -1:
            signals += 1
            if top is None:
                top = snap.get("question") or snap["market_id"]

    logger.info(f"Tail yield engine: {signals} signals")
    return {"agent": "tail_yield_engine", "signals": signals, "top": top}
