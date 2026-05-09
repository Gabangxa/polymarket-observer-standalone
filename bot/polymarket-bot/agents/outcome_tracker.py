# agents/outcome_tracker.py
# Resolves open signals by comparing entry state to current price data.
# Called each pipeline run after the signal engines so open signals are
# gradually closed out as their resolution windows expire.
#
# Resolution windows (configurable):
#   spread_harvesting  — 2 hours  (tight MM window)
#   neg_risk_overround — 6 hours  (over-round usually persists or tightens intraday)
#   mean_reversion     — 4 hours  (reversion window)

import json
import logging
import db

logger = logging.getLogger(__name__)

RESOLUTION_WINDOWS = {
    "spread_harvesting":  2,
    "neg_risk_overround": 6,
    "mean_reversion":     4,
    "odds_shift":         4,     # same reversion window as mean_reversion
    "binary_arb":         0.5,   # 30-min arb-persistence check
    "micro_spread_scalp": 2,     # same market-neutral window as spread_harvesting
}


def _resolve_spread(signal: dict, current_snapshot: dict):
    """
    Spread is captured if the market price stayed near entry (spread collected).
    Win  = |exit - entry| <= net_spread * 0.5 (price stable, spread pocketed)
    Loss = price moved more than half the net spread against the position
    PnL  = net_spread on win, -|price_move| on loss
    """
    meta        = signal.get("metadata") or {}
    entry_price = float(signal.get("entry_price") or meta.get("yes_price") or 0)
    net_spread  = float(meta.get("net_spread") or 0)
    exit_price  = float(current_snapshot.get("yes_price") or entry_price)

    price_move = abs(exit_price - entry_price)
    outcome    = price_move <= net_spread * 0.5
    pnl        = net_spread if outcome else -price_move
    return outcome, round(exit_price, 4), round(pnl, 6)


def _resolve_reversion(signal: dict, current_snapshot: dict):
    """
    Reversion wins if the price moved back from the sharp-move direction.
    Direction and entry price are read from signal metadata price_move block.
    PnL = directional difference (entry_price - exit_price) or reverse.
    """
    meta       = signal.get("metadata") or {}
    price_move = meta.get("price_move") or {}
    if isinstance(price_move, str):
        try:
            price_move = json.loads(price_move)
        except Exception:
            price_move = {}

    entry_price = float(price_move.get("end_price") or signal.get("entry_price") or 0)
    direction   = price_move.get("direction", "up")
    exit_price  = float(current_snapshot.get("yes_price") or entry_price)

    if direction == "up":
        outcome = exit_price < entry_price
        pnl     = entry_price - exit_price
    else:
        outcome = exit_price > entry_price
        pnl     = exit_price - entry_price

    return outcome, round(exit_price, 4), round(pnl, 6)


def _resolve_odds_shift(signal: dict, current_snapshot: dict):
    """
    Odds shift is treated as a mean-reversion signal.
    Entry = latest_price at signal time (the shifted price).
    Win  = price moved back toward prev_price (reversion occurred).
    PnL  = directional gain on the reversion position.

    direction="up"   → price rose → bet NO → win if exit < entry
    direction="down" → price fell → bet YES → win if exit > entry
    """
    meta        = signal.get("metadata") or {}
    entry_price = float(meta.get("latest_price") or signal.get("entry_price") or 0)
    direction   = meta.get("direction") or "up"
    exit_price  = float(current_snapshot.get("yes_price") or entry_price)

    if direction == "up":
        outcome = exit_price < entry_price
        pnl     = entry_price - exit_price
    else:
        outcome = exit_price > entry_price
        pnl     = exit_price - entry_price

    return outcome, round(exit_price, 4), round(pnl, 6)


def _resolve_binary_arb(signal: dict, current_snapshot: dict):
    """
    Arb persisted = yes_ask + no_ask still below 1.0 after the check window.
    Win  = arb still open (sum < 1.0)
    Loss = arb has closed (sum >= 1.0)
    PnL  = guaranteed_profit if win, else 0
    """
    meta              = signal.get("metadata") or {}
    guaranteed_profit = float(meta.get("guaranteed_profit") or 0)
    yes_ask           = float(current_snapshot.get("yes_ask") or 1)
    no_ask            = float(current_snapshot.get("no_ask") or 1)
    current_sum       = yes_ask + no_ask
    outcome           = current_sum < 1.0
    pnl               = guaranteed_profit if outcome else 0.0
    return outcome, round(current_sum, 4), round(pnl, 6)


def _resolve_micro_spread(signal: dict, current_snapshot: dict):
    """
    Micro-spread captured if market price stayed near entry (spread pocketed).
    Win  = |exit - entry_mid| <= spread * 0.5
    PnL  = potential_capture if win, else -|price_move|
    """
    meta              = signal.get("metadata") or {}
    best_bid          = float(meta.get("best_bid") or 0)
    best_ask          = float(meta.get("best_ask") or 0)
    entry_mid         = (best_bid + best_ask) / 2 if best_bid and best_ask else 0
    entry_spread      = float(meta.get("spread") or 0)
    potential_capture = float(meta.get("potential_capture") or 0)
    exit_price        = float(current_snapshot.get("yes_price") or entry_mid)
    price_move        = abs(exit_price - entry_mid)
    outcome           = price_move <= entry_spread * 0.5
    pnl               = potential_capture if outcome else -price_move
    return outcome, round(exit_price, 4), round(pnl, 6)


def _resolve_neg_risk(signal: dict, snapshots_by_market: dict):
    """
    Neg-risk wins if the over-round has decreased since signal time.
    Re-sums yes_prices for all outcome markets in the event.
    PnL is expressed in basis points (improvement in over-round).
    exit_price stores the current sum of prices for the event.
    """
    meta      = signal.get("metadata") or {}
    entry_sum = float(meta.get("sum_prices") or 1.0)
    outcomes  = meta.get("outcomes") or []

    current_prices = []
    for outcome_item in outcomes:
        mid = outcome_item.get("market_id")
        if mid and mid in snapshots_by_market:
            snap  = snapshots_by_market[mid]
            price = snap.get("yes_price")
            if price is not None:
                current_prices.append(float(price))

    if not current_prices:
        return None, None, None

    current_sum = sum(current_prices)
    outcome     = current_sum < entry_sum                     # over-round tightened = win
    pnl         = round((entry_sum - current_sum) * 100, 4)  # basis points change
    exit_price  = round(current_sum, 6)
    return outcome, exit_price, pnl


def run():
    logger.info("=== Outcome tracker starting ===")

    # Pull latest snapshot per market once — reused for all strategies
    latest_snapshots    = db.get_latest_snapshots(limit=500)
    snapshots_by_market = {s["market_id"]: s for s in latest_snapshots}

    resolved_total = 0
    skipped_total  = 0

    for strategy, window_hours in RESOLUTION_WINDOWS.items():
        signals = db.get_unresolved_signals(strategy=strategy, older_than_hours=window_hours)
        if not signals:
            continue

        logger.info(f"  {strategy}: {len(signals)} unresolved signal(s) past {window_hours}h window")

        for signal in signals:
            try:
                market_id = signal.get("market_id")

                if strategy == "spread_harvesting":
                    if not market_id or market_id not in snapshots_by_market:
                        skipped_total += 1
                        continue
                    outcome, exit_price, pnl = _resolve_spread(
                        signal, snapshots_by_market[market_id]
                    )

                elif strategy == "mean_reversion":
                    if not market_id or market_id not in snapshots_by_market:
                        skipped_total += 1
                        continue
                    outcome, exit_price, pnl = _resolve_reversion(
                        signal, snapshots_by_market[market_id]
                    )

                elif strategy == "odds_shift":
                    if not market_id or market_id not in snapshots_by_market:
                        skipped_total += 1
                        continue
                    outcome, exit_price, pnl = _resolve_odds_shift(
                        signal, snapshots_by_market[market_id]
                    )

                elif strategy == "neg_risk_overround":
                    outcome, exit_price, pnl = _resolve_neg_risk(
                        signal, snapshots_by_market
                    )
                    if outcome is None:
                        skipped_total += 1
                        continue

                elif strategy == "binary_arb":
                    if not market_id or market_id not in snapshots_by_market:
                        skipped_total += 1
                        continue
                    outcome, exit_price, pnl = _resolve_binary_arb(
                        signal, snapshots_by_market[market_id]
                    )

                elif strategy == "micro_spread_scalp":
                    if not market_id or market_id not in snapshots_by_market:
                        skipped_total += 1
                        continue
                    outcome, exit_price, pnl = _resolve_micro_spread(
                        signal, snapshots_by_market[market_id]
                    )

                else:
                    skipped_total += 1
                    continue

                db.update_signal_outcome(signal["id"], exit_price, pnl, outcome)
                resolved_total += 1
                logger.info(
                    f"    [{signal['id']}] {strategy} → "
                    f"{'WIN' if outcome else 'LOSS'} | "
                    f"exit={exit_price} | pnl={pnl:+.4f}"
                )

            except Exception as e:
                logger.warning(f"    Failed to resolve signal {signal['id']}: {e}")
                skipped_total += 1

    logger.info(
        f"Outcome tracker done — resolved: {resolved_total}, skipped: {skipped_total}"
    )
    return {
        "agent":    "outcome_tracker",
        "resolved": resolved_total,
        "skipped":  skipped_total,
    }
