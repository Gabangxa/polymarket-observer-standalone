# agents/reversion_engine.py
# Phase 3c: Mean reversion on thin-liquidity price shocks.
# Reads latest snapshots from DB, writes signals to DB.

import logging
from datetime import datetime, timezone
from config import (
    REVERSION_PRICE_MOVE_THRESHOLD,
    REVERSION_WINDOW_HOURS,
    REVERSION_MAX_OI,
)
import db
from agents.ev_calculator import size_recommendation

logger = logging.getLogger(__name__)


def _extract_oi(oi_data):
    if oi_data is None:
        return 0.0
    if isinstance(oi_data, (int, float)):
        return float(oi_data)
    if isinstance(oi_data, dict):
        for key in ("openInterest", "open_interest", "oi", "value", "total"):
            if key in oi_data:
                try:
                    return float(oi_data[key])
                except (TypeError, ValueError):
                    pass
    return 0.0


def _analyse_price_history(history, window_hours):
    if not history or len(history) < 2:
        return None

    history = sorted(history, key=lambda x: x.get("t", 0))
    cutoff  = datetime.now(timezone.utc).timestamp() - window_hours * 3600
    window  = [pt for pt in history if pt.get("t", 0) >= cutoff]

    if len(window) < 2:
        return None

    start = float(window[0].get("p", 0))
    end   = float(window[-1].get("p", 0))
    delta = abs(end - start)

    if delta < REVERSION_PRICE_MOVE_THRESHOLD:
        return None

    hours_elapsed = max(
        (window[-1].get("t", 0) - window[0].get("t", 0)) / 3600, 0.01
    )
    return {
        "start_price":   round(start, 4),
        "end_price":     round(end, 4),
        "delta":         round(delta, 4),
        "direction":     "up" if end > start else "down",
        "hours_elapsed": round(hours_elapsed, 2),
        "velocity":      round(delta / hours_elapsed, 4),
    }


def _analyse_snapshot(snapshot):
    history  = snapshot.get("price_history") or []
    oi_value = _extract_oi(snapshot.get("open_interest"))

    if oi_value > REVERSION_MAX_OI and oi_value > 0:
        return None

    move = _analyse_price_history(history, REVERSION_WINDOW_HOURS)
    if not move:
        return None

    oi_penalty   = min(oi_value / REVERSION_MAX_OI, 1.0) if REVERSION_MAX_OI > 0 else 0
    signal_score = move["delta"] * (1 - oi_penalty * 0.5)

    # EV: q = price before move (expected revert target), p = current distorted price
    # direction="up"   → bet NO  (price rose, expect fall)
    # direction="down" → bet YES (price fell, expect rise)
    q    = move["start_price"]
    p    = move["end_price"]
    side = "no" if move["direction"] == "up" else "yes"
    sizing = size_recommendation(q=q, p=p, side=side)

    signal = {
        "strategy":      "mean_reversion",
        "market_id":     snapshot["market_id"],
        "event_slug":    snapshot.get("event_slug"),
        "question":      snapshot.get("question"),
        "tags":          snapshot.get("tags") or [],
        "current_price": move["end_price"],
        "price_move":    move,
        "open_interest": oi_value,
        "signal_score":  round(signal_score, 4),
        "trigger": (
            f"Price moved {move['delta']:.4f} {move['direction']} "
            f"{move['start_price']:.4f} → {move['end_price']:.4f} "
            f"over {move['hours_elapsed']:.1f}h "
            f"(threshold {REVERSION_PRICE_MOVE_THRESHOLD}, OI ~${oi_value:,.0f})."
        ),
    }
    if sizing:
        signal.update(sizing)
    return signal


def run():
    logger.info("=== Reversion engine starting ===")
    snapshots = db.get_latest_snapshots(limit=200)

    if not snapshots:
        logger.info("No snapshots found")
        return {"agent": "reversion_engine", "signals": 0}

    logger.info(f"Analysing {len(snapshots)} snapshots")
    signals = []
    for snapshot in snapshots:
        signal = _analyse_snapshot(snapshot)
        if signal:
            row_id = db.insert_signal(signal)
            if row_id != -1:
                signals.append(signal)
                logger.info(
                    f"  SIGNAL [{row_id}]: {str(signal['question'])[:50]} | "
                    f"move={signal['price_move']['delta']:.4f} "
                    f"{signal['price_move']['direction']} | "
                    f"OI=${signal['open_interest']:,.0f}"
                )

    return {
        "agent":   "reversion_engine",
        "signals": len(signals),
        "top":     signals[0]["question"] if signals else None,
    }
