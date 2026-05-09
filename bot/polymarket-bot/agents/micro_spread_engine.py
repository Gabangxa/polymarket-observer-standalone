import logging

import db
from config import MICRO_SPREAD_THRESHOLD

logger = logging.getLogger(__name__)


def run():
    logger.info("=== Micro-spread engine starting ===")

    snapshots = db.get_latest_snapshots()
    signals = 0
    top = None

    for snap in snapshots:
        yes_price = snap.get("yes_price")
        spread    = snap.get("spread")

        if yes_price is None or spread is None:
            continue
        try:
            yes_price = float(yes_price)
            spread    = float(spread)
        except (TypeError, ValueError):
            continue

        if spread < MICRO_SPREAD_THRESHOLD:
            continue

        best_bid        = yes_price - spread / 2
        best_ask        = yes_price + spread / 2
        optimal_bid     = best_bid + 0.01
        optimal_ask     = best_ask - 0.01
        potential_capture = optimal_ask - optimal_bid
        score = min(spread / 0.10, 1.0)

        signal = {
            "strategy":         "micro_spread_scalp",
            "market_id":        snap["market_id"],
            "event_slug":       snap.get("event_slug"),
            "question":         snap.get("question"),
            "signal_score":     round(score, 4),
            "spread":           spread,
            "best_bid":         round(best_bid, 6),
            "best_ask":         round(best_ask, 6),
            "optimal_bid":      round(optimal_bid, 6),
            "optimal_ask":      round(optimal_ask, 6),
            "potential_capture": round(potential_capture, 6),
            "sizing_note": (
                f"Passive market-making. Capture {potential_capture:.4f}/share if both sides fill. "
                f"Risk: adverse selection — informed flow takes your passive order and moves price. "
                f"Size 1–2 lots max; losses compound on adversarial fills."
            ),
            "trigger": (
                f"Spread {spread:.4f} ≥ threshold {MICRO_SPREAD_THRESHOLD} — "
                f"optimal bid {optimal_bid:.4f} / ask {optimal_ask:.4f}, "
                f"potential capture {potential_capture:.4f}/share."
            ),
        }

        sid = db.insert_signal(signal)
        if sid != -1:
            signals += 1
            if top is None:
                top = snap.get("question") or snap["market_id"]

    logger.info(f"Micro-spread engine: {signals} signals")
    return {"agent": "micro_spread_engine", "signals": signals, "top": top}
