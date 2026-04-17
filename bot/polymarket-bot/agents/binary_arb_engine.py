import logging

import db
from config import ARB_THRESHOLD

logger = logging.getLogger(__name__)


def _get_arb_snapshots() -> list[dict]:
    """Fetch latest snapshot per market where both yes_ask and no_ask are available."""
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT DISTINCT ON (s.market_id)
                    s.market_id, s.yes_ask, s.no_ask, s.collected_at,
                    m.question, m.event_slug
                FROM snapshots s
                JOIN markets m ON m.market_id = s.market_id
                WHERE s.yes_ask IS NOT NULL AND s.no_ask IS NOT NULL
                ORDER BY s.market_id, s.collected_at DESC
            """)
            return [dict(r) for r in cur.fetchall()]


def run():
    logger.info("=== Binary arb engine starting ===")

    snapshots = _get_arb_snapshots()
    signals   = 0
    top       = None

    for snap in snapshots:
        try:
            yes_ask = float(snap["yes_ask"])
            no_ask  = float(snap["no_ask"])
        except (TypeError, ValueError):
            continue

        if yes_ask + no_ask >= ARB_THRESHOLD:
            continue

        guaranteed_profit = round(1.00 - (yes_ask + no_ask), 4)
        score = min(guaranteed_profit / 0.05, 1.0)

        signal = {
            "strategy":          "binary_arb",
            "market_id":         snap["market_id"],
            "event_slug":        snap.get("event_slug"),
            "signal_score":      round(score, 4),
            "buy_yes_at":        yes_ask,
            "buy_no_at":         no_ask,
            "total_cost":        round(yes_ask + no_ask, 4),
            "guaranteed_profit": guaranteed_profit,
            "trigger": (
                f"YES ask {yes_ask:.4f} + NO ask {no_ask:.4f} = {yes_ask + no_ask:.4f} "
                f"(threshold {ARB_THRESHOLD}) — "
                f"guaranteed profit {guaranteed_profit:.4f}/share."
            ),
        }

        sid = db.insert_signal(signal)
        if sid != -1:
            signals += 1
            if top is None:
                top = snap.get("question") or snap["market_id"]

    logger.info(f"Binary arb engine: {signals} signals")
    return {"agent": "binary_arb_engine", "signals": signals, "top": top}
