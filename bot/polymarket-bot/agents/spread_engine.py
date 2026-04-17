# agents/spread_engine.py
# Phase 3a: Spread harvesting — flag markets where spread > 2x round-trip fees.
# Reads from snapshots table, writes signals to signals table.

import logging
from config import (
    SPREAD_FEE_MULTIPLE, SPREAD_MIN_SIGNAL_SCORE,
    FEE_RATES, DEFAULT_FEE_RATE,
)
import db

logger = logging.getLogger(__name__)


def _estimate_fee(price, tags, fee_rate_bps=0):
    if fee_rate_bps and fee_rate_bps > 0:
        rate = fee_rate_bps / 10_000
        return rate * price * (price * (1 - price))
    rate, exp = DEFAULT_FEE_RATE
    for tag in tags:
        for category, params in FEE_RATES.items():
            if category in tag.lower():
                rate, exp = params
                break
    return rate * price * (price * (1 - price)) ** exp


def _analyse_snapshot(snapshot):
    spread    = snapshot.get("spread")
    midpoint  = snapshot.get("midpoint")
    yes_price = snapshot.get("yes_price")
    tags      = snapshot.get("tags") or []
    fee_bps   = float(snapshot.get("fee_rate_bps") or 0)

    if not spread or spread <= 0 or yes_price is None:
        return None
    if not midpoint or midpoint <= 0:
        midpoint = yes_price

    fee_rt = _estimate_fee(float(midpoint), tags, fee_bps) * 2
    if fee_rt <= 0:
        return None

    fee_multiple = float(spread) / fee_rt
    if fee_multiple < SPREAD_FEE_MULTIPLE:
        return None

    net_spread   = float(spread) - fee_rt
    signal_score = net_spread / float(spread)

    if signal_score < SPREAD_MIN_SIGNAL_SCORE:
        return None

    return {
        "strategy":       "spread_harvesting",
        "market_id":      snapshot["market_id"],
        "event_slug":     snapshot.get("event_slug"),
        "question":       snapshot.get("question"),
        "tags":           tags,
        "yes_price":      round(float(yes_price), 4),
        "spread":         round(float(spread), 4),
        "fee_round_trip": round(fee_rt, 6),
        "net_spread":     round(net_spread, 4),
        "fee_multiple":   round(fee_multiple, 2),
        "signal_score":   round(signal_score, 4),
        "trigger": (
            f"Spread {float(spread):.4f} is {fee_multiple:.1f}× the round-trip fee "
            f"(threshold {SPREAD_FEE_MULTIPLE}×). Net edge after fees: {net_spread:.4f}/share."
        ),
    }


def run():
    logger.info("=== Spread engine starting ===")
    snapshots = db.get_latest_snapshots(limit=200)
    if not snapshots:
        logger.info("No snapshots found")
        return {"agent": "spread_engine", "signals": 0}

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
                    f"spread={signal['spread']:.4f} | score={signal['signal_score']:.3f}"
                )

    return {
        "agent":   "spread_engine",
        "signals": len(signals),
        "top":     signals[0]["question"] if signals else None,
    }
