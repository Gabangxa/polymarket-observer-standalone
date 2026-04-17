# agents/neg_risk_engine.py
# Phase 3b: Neg-risk over-round detection.
# Reads neg-risk snapshots grouped by event, writes signals to DB.

import logging
from config import NEG_RISK_OVERROUND_THRESHOLD, NEG_RISK_MIN_OUTCOMES
import db
from agents.ev_calculator import yes_ev

logger = logging.getLogger(__name__)


def _analyse_event(event_slug, snapshots):
    if len(snapshots) < NEG_RISK_MIN_OUTCOMES:
        return None

    prices = []
    for s in snapshots:
        price = s.get("yes_price")
        if price is not None and float(price) > 0:
            prices.append((s.get("question", "?"), float(price), s.get("market_id")))

    if len(prices) < NEG_RISK_MIN_OUTCOMES:
        return None

    total = sum(p for _, p, _ in prices)
    if total <= NEG_RISK_OVERROUND_THRESHOLD:
        return None

    overround = total - 1.0
    n = len(prices)

    # EV per outcome: fair price = 1/n (uniform). Selling NO on outcome i:
    # q_no = 1 - (1/n), p_no = 1 - p_i. EV_no = yes_ev(q_no, p_no).
    fair_p = 1.0 / n
    ev_per_outcome = [
        round(yes_ev(1.0 - fair_p, 1.0 - p), 4)
        for _, p, _ in prices
    ]
    avg_ev = round(sum(ev_per_outcome) / n, 4) if ev_per_outcome else 0.0

    return {
        "strategy":     "neg_risk_overround",
        "market_id":    None,
        "event_slug":   event_slug,
        "num_outcomes": n,
        "sum_prices":   round(total, 6),
        "overround":    round(overround, 6),
        "edge_pct":     round(overround * 100, 4),
        "signal_score": round(overround, 6),
        "ev":           avg_ev,
        "ev_side":      "NO (all outcomes)",
        "sizing_note":  (
            f"Sell NO on all {n} outcomes | "
            f"avg EV={avg_ev*100:.1f}% per outcome | "
            f"over-round={overround*100:.2f}c"
        ),
        "outcomes": [
            {
                "question": q, "yes_price": round(p, 4), "market_id": mid,
                "ev_no": round(yes_ev(1.0 - fair_p, 1.0 - p), 4),
            }
            for q, p, mid in sorted(prices, key=lambda x: x[1], reverse=True)
        ],
        "trigger": (
            f"Sum of {n} YES prices = {total:.4f} "
            f"(threshold {NEG_RISK_OVERROUND_THRESHOLD}). "
            f"Over-round: {overround*100:.2f}¢ — sell NO on all {n} outcomes."
        ),
    }


def run():
    logger.info("=== Neg-risk engine starting ===")
    events_by_slug = db.get_neg_risk_snapshots_by_event()

    if not events_by_slug:
        logger.info("No neg-risk snapshots found")
        return {"agent": "neg_risk_engine", "events_checked": 0, "signals": 0}

    logger.info(f"Checking {len(events_by_slug)} neg-risk events")
    signals = []
    for event_slug, snapshots in events_by_slug.items():
        signal = _analyse_event(event_slug, snapshots)
        if signal:
            row_id = db.insert_signal(signal)
            if row_id != -1:
                signals.append(signal)
                logger.info(
                    f"  SIGNAL [{row_id}]: {event_slug} | "
                    f"sum={signal['sum_prices']:.4f} | "
                    f"over-round={signal['edge_pct']:.2f}c"
                )

    return {
        "agent":          "neg_risk_engine",
        "events_checked": len(events_by_slug),
        "signals":        len(signals),
        "top":            signals[0]["event_slug"] if signals else None,
    }
