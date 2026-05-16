# agents/spread_engine.py
# Phase 3a: Spread harvesting — flag markets where spread > 2x round-trip fees.
# Reads from snapshots table, writes signals to signals table.

import logging
from config import (
    SPREAD_FEE_MULTIPLE, SPREAD_MIN_SIGNAL_SCORE,
    SPREAD_MIN_YES_PRICE, SPREAD_MAX_YES_PRICE,
    FEE_RATES, DEFAULT_FEE_RATE,
    KELLY_FRACTION, MAX_POSITION_PCT,
)
import db


def _kelly_for_score(signal_score: float) -> float:
    """
    Fraction of bankroll to bet, scaled linearly by signal_score.
    Full ¼-Kelly position (= MAX_POSITION_PCT × KELLY_FRACTION bankroll) at score=1.0;
    proportionally smaller below. Order_manager._size_from_signal multiplies this
    by bankroll and caps at MAX_POSITION_PCT, so the value here never exceeds the cap.

    Spread harvesting has no clean directional Kelly model (the edge is in the
    microstructure, not the outcome probability) — so we use signal quality as
    the sizing proxy. A signal at the minimum threshold gets 60% of the slot a
    score-1.0 signal gets, which matches the relative edge captured.
    """
    return round(KELLY_FRACTION * MAX_POSITION_PCT * max(0.0, signal_score), 6)

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
    yes_ask   = snapshot.get("yes_ask")
    tags      = snapshot.get("tags") or []
    fee_bps   = float(snapshot.get("fee_rate_bps") or 0)

    if not spread or spread <= 0 or yes_price is None:
        return None
    yes_price_f = float(yes_price)
    if not (SPREAD_MIN_YES_PRICE <= yes_price_f <= SPREAD_MAX_YES_PRICE):
        return None
    if not midpoint or midpoint <= 0:
        midpoint = yes_price

    neg_risk  = bool(snapshot.get("neg_risk", False))
    spread_f  = float(spread)
    fee_rt = _estimate_fee(float(midpoint), tags, fee_bps) * 2

    if fee_rt <= 0:
        # Zero-fee market (e.g. geopolitics) — entire spread is pure edge.
        # Score normalised against 10¢ reference spread; minimum 1¢ to avoid noise.
        if spread_f < 0.01:
            return None
        signal_score = min(spread_f / 0.10, 1.0)
        return {
            "strategy":       "spread_engine",
            "market_id":      snapshot["market_id"],
            "event_slug":     snapshot.get("event_slug"),
            "question":       snapshot.get("question"),
            "tags":           tags,
            "neg_risk":       neg_risk,
            "yes_price":      round(float(yes_price), 4),
            "yes_ask":        round(float(yes_ask), 4) if yes_ask is not None else None,
            "spread":         round(spread_f, 4),
            "fee_round_trip": 0.0,
            "net_spread":     round(spread_f, 4),
            "fee_multiple":   None,   # infinite — fee-free market
            "signal_score":   round(signal_score, 4),
            "kelly_fraction": _kelly_for_score(signal_score),
            "sizing_note": (
                f"Fee-free. Net edge {spread_f:.4f}/share. "
                f"Adverse selection risk ≈ {spread_f:.4f}/share (full spread). "
                f"Suggested lot: (bankroll × 1%) ÷ {spread_f:.4f} shares."
            ),
            "trigger": (
                f"Fee-free market. Spread {spread_f:.4f} is pure edge "
                f"(no round-trip cost). Net edge: {spread_f:.4f}/share."
            ),
        }

    fee_multiple = spread_f / fee_rt
    if fee_multiple < SPREAD_FEE_MULTIPLE:
        return None

    net_spread   = spread_f - fee_rt
    signal_score = net_spread / spread_f

    if signal_score < SPREAD_MIN_SIGNAL_SCORE:
        return None

    return {
        "strategy":       "spread_engine",
        "market_id":      snapshot["market_id"],
        "event_slug":     snapshot.get("event_slug"),
        "question":       snapshot.get("question"),
        "tags":           tags,
        "neg_risk":       neg_risk,
        "yes_price":      round(float(yes_price), 4),
        "yes_ask":        round(float(yes_ask), 4) if yes_ask is not None else None,
        "spread":         round(spread_f, 4),
        "fee_round_trip": round(fee_rt, 6),
        "net_spread":     round(net_spread, 4),
        "fee_multiple":   round(fee_multiple, 2),
        "signal_score":   round(signal_score, 4),
        "kelly_fraction": _kelly_for_score(signal_score),
        "sizing_note": (
            f"Net edge {net_spread:.4f}/share after {fee_rt:.4f} round-trip fee. "
            f"Adverse selection risk ≈ {spread_f:.4f}/share (full spread). "
            f"Suggested lot: (bankroll × 1%) ÷ {spread_f:.4f} shares."
        ),
        "trigger": (
            f"Spread {spread_f:.4f} is {fee_multiple:.1f}× the round-trip fee "
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
