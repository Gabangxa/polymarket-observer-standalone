# agents/hindsight_logger.py
# Closes the feedback loop: when a market fully resolves (YES/NO),
# find signals emitted before it resolved and record the true outcome.
#
# Covers:
#   odds_shift         — directional: direction field determines correct outcome
#   tail_yield_engine — YES bet: always correct when resolved YES
#
# Skips: spread_harvesting, neg_risk_overround, binary_arb, micro_spread_scalp
#   (those are handled by outcome_tracker's snapshot-comparison window)
#
# Resolution mapping:
#   resolutionPrice == "1" → market resolved YES
#   resolutionPrice == "0" → market resolved NO

import logging
import api
import db

logger = logging.getLogger(__name__)

DIRECTIONAL_STRATEGIES = {"odds_shift"}
TAIL_STRATEGIES        = {"tail_yield_engine"}


def _resolution_price(market_data: dict) -> float | None:
    """Extract numeric resolution price from Gamma API response."""
    raw = market_data.get("resolutionPrice")
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _signal_correct(signal: dict, resolved_yes: bool) -> bool | None:
    """
    Determine if a signal was correct given the final resolution.
    Returns None if outcome cannot be determined from the signal data.
    """
    strategy = signal.get("strategy")

    # Tail yield is always a YES bet — no direction field needed
    if strategy == "tail_yield_engine":
        return resolved_yes

    meta      = signal.get("metadata") or {}
    direction = meta.get("direction") or signal.get("direction")
    if not direction:
        return None

    if direction == "up":
        # Signal predicted price will fall → bet NO → correct if resolved NO
        return not resolved_yes
    elif direction == "down":
        # Signal predicted price will rise → bet YES → correct if resolved YES
        return resolved_yes
    return None


def run() -> dict:
    logger.info("=== Hindsight logger starting ===")

    # Fetch all unresolved signals that need resolution-based outcome scoring
    all_unresolved = []
    for strategy in DIRECTIONAL_STRATEGIES | TAIL_STRATEGIES:
        signals = db.get_unresolved_signals(strategy=strategy, older_than_hours=0)
        all_unresolved.extend(signals)

    if not all_unresolved:
        logger.info("No unresolved directional signals")
        return {"agent": "hindsight_logger", "resolved": 0, "skipped": 0}

    # Deduplicate market_ids to minimise API calls
    market_ids = list({s["market_id"] for s in all_unresolved if s.get("market_id")})
    logger.info(
        f"Checking resolution for {len(market_ids)} markets "
        f"({len(all_unresolved)} signals)"
    )

    # Fetch resolution status from Gamma API
    resolved_markets: dict[str, float] = {}
    for market_id in market_ids:
        try:
            data = api.get_market_resolution(market_id)
            if data and data.get("closed"):
                price = _resolution_price(data)
                if price is not None:
                    resolved_markets[market_id] = price
        except Exception as e:
            logger.warning(f"  Could not fetch resolution for {market_id}: {e}")

    if not resolved_markets:
        logger.info("No watched markets have resolved yet")
        return {"agent": "hindsight_logger", "resolved": 0, "skipped": 0}

    logger.info(f"{len(resolved_markets)} market(s) resolved, scoring signals...")

    resolved_count = 0
    skipped_count  = 0

    for signal in all_unresolved:
        market_id = signal.get("market_id")
        if market_id not in resolved_markets:
            skipped_count += 1
            continue

        resolution_price = resolved_markets[market_id]
        resolved_yes     = resolution_price >= 0.5

        outcome = _signal_correct(signal, resolved_yes)
        if outcome is None:
            logger.debug(f"  Signal {signal['id']}: no direction, skipping")
            skipped_count += 1
            continue

        meta       = signal.get("metadata") or {}
        exit_price = float(resolution_price)

        if signal.get("strategy") == "tail_yield_engine":
            # Tail yield: bought YES at current_price, resolved at 1.0 or 0.0
            entry_price = float(meta.get("current_price") or 0)
            pnl = exit_price - entry_price
        else:
            # Directional signals: entry via trade_price / latest_price / yes_price
            entry_price = float(
                meta.get("trade_price") or meta.get("latest_price") or meta.get("yes_price") or 0
            )
            direction = meta.get("direction") or ""
            if direction == "down":   # bet YES
                pnl = exit_price - entry_price
            else:                     # bet NO — paid (1-entry), wins (1-exit)
                pnl = (1.0 - entry_price) - (1.0 - exit_price)

        db.update_signal_outcome(signal["id"], exit_price, round(pnl, 6), outcome)
        resolved_count += 1
        logger.info(
            f"  [{signal['id']}] {signal.get('strategy')} "
            f"{'CORRECT' if outcome else 'WRONG'} | "
            f"resolved={'YES' if resolved_yes else 'NO'} | pnl={pnl:+.4f}"
        )

    logger.info(f"Hindsight logger done — resolved: {resolved_count}, skipped: {skipped_count}")
    return {
        "agent":    "hindsight_logger",
        "resolved": resolved_count,
        "skipped":  skipped_count,
    }
