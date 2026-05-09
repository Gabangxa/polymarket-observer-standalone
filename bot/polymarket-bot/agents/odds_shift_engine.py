# agents/odds_shift_engine.py
# Detects significant price shifts between the two most recent snapshots.
#
# Logic:
#   - For each market, compare the latest snapshot vs the one before it.
#   - If |price_delta| >= ODDS_SHIFT_THRESHOLD, emit an "odds_shift" signal.
#   - Annotates with velocity (delta per hour) and an EV estimate assuming
#     mean reversion back to the pre-shift price.

import logging
from datetime import datetime, timezone

import db
from agents.ev_calculator import size_recommendation
from config import ODDS_SHIFT_THRESHOLD, ODDS_SHIFT_MIN_HOURS

logger = logging.getLogger(__name__)


def _analyse_pair(row: dict) -> dict | None:
    latest_price = row.get("latest_price")
    prev_price   = row.get("prev_price")
    if latest_price is None or prev_price is None:
        return None

    latest_price = float(latest_price)
    prev_price   = float(prev_price)
    delta = latest_price - prev_price   # positive = price moved up

    if abs(delta) < ODDS_SHIFT_THRESHOLD:
        return None

    # Compute velocity (delta per hour)
    latest_at = row.get("latest_at")
    prev_at   = row.get("prev_at")
    hours_elapsed = 0.0
    if latest_at and prev_at:
        if isinstance(latest_at, str):
            latest_at = datetime.fromisoformat(latest_at.replace("Z", "+00:00"))
        if isinstance(prev_at, str):
            prev_at = datetime.fromisoformat(prev_at.replace("Z", "+00:00"))
        hours_elapsed = (latest_at - prev_at).total_seconds() / 3600

    # Skip pairs shorter than the minimum window — don't clamp elapsed time, as that
    # would inflate velocity and produce misleading signals on sub-threshold windows.
    if hours_elapsed < ODDS_SHIFT_MIN_HOURS:
        return None

    velocity = round(abs(delta) / hours_elapsed, 4)

    # EV: assume price will revert to pre-shift level.
    # If delta > 0 (price rose), bet NO (expect it to fall back).
    # If delta < 0 (price fell), bet YES (expect it to rise back).
    direction = "up" if delta > 0 else "down"
    side      = "no" if direction == "up" else "yes"
    q         = prev_price   # estimated true probability = pre-shift price

    sizing = size_recommendation(q=q, p=latest_price, side=side)

    # Normalise score to 0.0–1.0: 5¢ move → ~0.33, 15¢ move → 1.0 (ceiling)
    signal_score = round(min(abs(delta) / 0.15, 1.0), 4)

    signal = {
        "strategy":       "odds_shift",
        "market_id":      row["market_id"],
        "event_slug":     row.get("event_slug"),
        "question":       row.get("question"),
        "tags":           row.get("tags") or [],
        "prev_price":     round(prev_price, 4),
        "latest_price":   round(latest_price, 4),
        "delta":          round(delta, 4),
        "direction":      direction,
        "hours_elapsed":  round(hours_elapsed, 2),
        "velocity":       velocity,
        "signal_score":   signal_score,
        "trigger": (
            f"Price shifted {delta:+.4f} ({direction}) "
            f"{prev_price:.4f} → {latest_price:.4f} "
            f"over {hours_elapsed:.1f}h "
            f"(threshold {ODDS_SHIFT_THRESHOLD}, velocity: {velocity:.4f}/h)."
        ),
    }

    if sizing:
        signal.update(sizing)

    return signal


def run() -> dict:
    logger.info("=== Odds shift engine starting ===")

    # Use window-based pairs: compare latest snapshot against one from
    # ODDS_SHIFT_MIN_HOURS–2h ago so the elapsed time is always meaningful.
    pairs = db.get_snapshot_pairs(min_hours=ODDS_SHIFT_MIN_HOURS, max_hours=2.0)
    if not pairs:
        logger.info(f"No snapshot pairs in the {ODDS_SHIFT_MIN_HOURS}h–2h window yet")
        return {"agent": "odds_shift_engine", "checked": 0, "signals": 0}

    logger.info(f"Checking {len(pairs)} market snapshot pairs")
    signals = []

    for row in pairs:
        signal = _analyse_pair(row)
        if not signal:
            continue
        row_id = db.insert_signal(signal)
        if row_id != -1:
            signals.append(signal)
            logger.info(
                f"  SIGNAL [{row_id}]: {str(signal['question'])[:50]} | "
                f"delta={signal['delta']:+.4f} ({signal['direction']}) | "
                f"score={signal['signal_score']:.4f}"
            )

    return {
        "agent":   "odds_shift_engine",
        "checked": len(pairs),
        "signals": len(signals),
        "top":     signals[0]["question"] if signals else None,
    }
