# agents/neg_risk_engine.py
# Phase 3b: Neg-risk over-round detection.
# Reads neg-risk snapshots grouped by event, writes signals to DB.

import logging
from config import NEG_RISK_MAKER_THRESHOLD, NEG_RISK_TAKER_THRESHOLD, NEG_RISK_MIN_OUTCOMES
import db
from agents.ev_calculator import yes_ev

logger = logging.getLogger(__name__)


def _analyse_event(event_slug, snapshots):
    if len(snapshots) < NEG_RISK_MIN_OUTCOMES:
        return None

    # Collect priced legs. Track missing counts separately for diagnostics.
    records = []
    missing_ask = 0
    missing_mid = 0
    for s in snapshots:
        midpoint = s.get("yes_price")
        ask      = s.get("yes_ask")
        if midpoint is None or float(midpoint) <= 0:
            missing_mid += 1
            continue
        if ask is None or float(ask) <= 0:
            missing_ask += 1
            continue
        token_ids = s.get("token_ids") or []
        records.append({
            "question":     s.get("question", "?"),
            "market_id":    s.get("market_id"),
            "midpoint":     float(midpoint),
            "ask":          float(ask),
            "no_price":     float(s["no_price"]) if s.get("no_price") is not None else round(1.0 - float(midpoint), 6),
            "no_ask":       float(s["no_ask"])   if s.get("no_ask")   is not None else None,
            "yes_token_id": token_ids[0] if len(token_ids) > 0 else None,
            "no_token_id":  token_ids[1] if len(token_ids) > 1 else None,
        })

    total_legs   = len(snapshots)
    priced_legs  = len(records)
    uncovered    = total_legs - priced_legs
    all_complete = (uncovered == 0)

    if priced_legs < NEG_RISK_MIN_OUTCOMES:
        logger.info(
            f"  Skipped {event_slug}: only {priced_legs}/{total_legs} legs priced "
            f"(missing_ask={missing_ask}, missing_mid={missing_mid}, need ≥{NEG_RISK_MIN_OUTCOMES})"
        )
        return None

    sum_asks = sum(r["ask"] for r in records)
    sum_mids = sum(r["midpoint"] for r in records)

    # Taker arb: buy YES on every outcome — one uncovered leg winning = full loss.
    # Completeness is mandatory: you cannot leave any outcome unhedged.
    is_taker_arb = all_complete and (sum_asks < NEG_RISK_TAKER_THRESHOLD)

    # Maker arb: sell YES only on the liquid legs you can trade.
    # An uncovered leg winning is a bonus — you collected all premiums and owe $0 on it.
    # Partial coverage is valid; only the priced legs need to show over-round.
    is_maker_arb = sum_mids > NEG_RISK_MAKER_THRESHOLD

    if not is_taker_arb and not is_maker_arb:
        return None

    # Sanity floor for taker: sum < 0.80 → missing data, not real arb.
    if is_taker_arb and sum_asks < 0.80:
        logger.warning(
            f"  Skipped {event_slug}: sum_asks={sum_asks:.4f} below sanity floor 0.80 — "
            f"likely stale/missing orderbook data, not real arbitrage"
        )
        return None

    # Sanity ceiling for partial-coverage maker: sum > 1.20 with uncovered legs
    # suggests stale/inflated pricing rather than genuine over-round.
    if is_maker_arb and not all_complete and sum_mids > 1.20:
        logger.warning(
            f"  Skipped {event_slug}: sum_mids={sum_mids:.4f} exceeds ceiling 1.20 "
            f"with {uncovered} uncovered leg(s) — likely stale data, not real over-round"
        )
        return None

    n = len(records)
    # Taker arb (instant fill) takes priority when both are present
    if is_taker_arb:
        arb_type     = "taker"
        total        = sum_asks
        overround    = 1.0 - total
        ev_side      = "YES (all outcomes, taker)"
        threshold    = NEG_RISK_TAKER_THRESHOLD
        trigger_note = (
            f"Sum of YES asks = {total:.4f} < {threshold} — "
            f"buy all {n} YES outcomes for guaranteed profit."
        )
    else:
        arb_type  = "maker"
        total     = sum_mids
        overround = total - 1.0
        ev_side   = "YES SELL (liquid outcomes, maker)"
        threshold = NEG_RISK_MAKER_THRESHOLD
        if uncovered > 0:
            trigger_note = (
                f"Sum of YES mids on {n}/{total_legs} priced legs = {total:.4f} > {threshold} — "
                f"sell YES on {n} liquid outcomes. "
                f"{uncovered} unpriced leg(s) winning is a bonus: "
                f"keep all collected premiums, owe $0."
            )
        else:
            trigger_note = (
                f"Sum of YES mids = {total:.4f} > {threshold} — "
                "sell YES on all outcomes. Collect sum(YES_mids) > $1; "
                "owe exactly $1 at resolution. Profit locked at fill."
            )

    # fair_p based on total legs so EV accounts for the full outcome space
    fair_p = 1.0 / total_legs
    ev_per_outcome = [
        round(yes_ev(1.0 - fair_p, 1.0 - r["midpoint"]), 4)
        for r in records
    ]
    avg_ev = round(sum(ev_per_outcome) / n, 4) if ev_per_outcome else 0.0

    return {
        "strategy":       "neg_risk_overround",
        "arb_type":       arb_type,
        "market_id":      None,
        "event_slug":     event_slug,
        "num_outcomes":   n,
        "total_legs":     total_legs,
        "uncovered_legs": uncovered,
        "sum_prices":     round(total, 6),
        "sum_asks":       round(sum_asks, 6),
        "sum_mids":       round(sum_mids, 6),
        "overround":      round(overround, 6),
        "edge_pct":       round(overround * 100, 4),
        "signal_score":   round(overround, 6),
        "ev":             avg_ev,
        "ev_side":        ev_side,
        "sizing_note":    (
            f"{ev_side} | "
            f"avg EV={avg_ev*100:.1f}% per outcome | "
            f"over-round={overround*100:.2f}c"
            + (f" | {uncovered}/{total_legs} legs unpriced (bonus if won)" if uncovered > 0 else "")
        ),
        "outcomes": [
            {
                "question":     r["question"],
                "market_id":    r["market_id"],
                "yes_price":    round(r["midpoint"], 4),
                "yes_ask":      round(r["ask"], 4),
                "yes_token_id": r["yes_token_id"],
                "no_price":     round(r["no_price"], 4),
                "no_ask":       round(r["no_ask"], 4) if r["no_ask"] is not None else None,
                "no_token_id":  r["no_token_id"],
                "ev_no":        round(yes_ev(1.0 - fair_p, 1.0 - r["midpoint"]), 4),
            }
            for r in sorted(records, key=lambda x: x["midpoint"], reverse=True)
        ],
        "trigger": trigger_note,
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
