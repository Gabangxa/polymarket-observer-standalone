# agents/data_collector.py
# Phase 2: Collect a full data snapshot for each watched market.
# Output: inserted into the snapshots table in Postgres.

import logging
from datetime import datetime, timezone

import api
import db
from config import PRICE_HISTORY_FIDELITY, PRICE_HISTORY_LIMIT

logger = logging.getLogger(__name__)
FIDELITY_MAP = {"1m": 1, "5m": 5, "1h": 60, "1d": 1440}


def _collect_market_snapshot(market):
    market_id = market["market_id"]
    token_ids = market.get("token_ids") or []
    yes_token = token_ids[0] if token_ids else None          # YES token (index 0)
    no_token  = token_ids[1] if len(token_ids) > 1 else None # NO token (index 1)

    snapshot = {
        "market_id":     market_id,
        "collected_at":  datetime.now(timezone.utc),
        "yes_price":     None,
        "no_price":      None,
        "spread":        None,
        "midpoint":      None,
        "fee_rate_bps":  0,
        "open_interest": None,
        "price_history": [],
        "top_holders":   [],
        "recent_trades": [],
        "errors":        [],
        "yes_ask":       None,
        "no_ask":        None,
    }

    if not yes_token:
        snapshot["errors"].append("no token_ids")
        return snapshot

    # Fetch YES ask price (price to buy a YES share)
    try:
        yes_ask = api.get_price(yes_token, side="buy")
        if yes_ask is not None and yes_ask > 0:
            snapshot["yes_ask"] = yes_ask
    except Exception as e:
        snapshot["errors"].append(f"yes_ask: {e}")
        logger.warning(f"  yes_ask failed for {market_id}: {e}")

    # Fetch NO ask price (price to buy a NO share)
    try:
        if no_token:
            no_ask = api.get_price(no_token, side="buy")
            if no_ask is not None and no_ask > 0:
                snapshot["no_ask"] = no_ask
    except Exception as e:
        snapshot["errors"].append(f"no_ask: {e}")
        logger.warning(f"  no_ask failed for {market_id}: {e}")

    # /spread only returns {"spread": "value"} — no longer includes mid or sell
    try:
        sd = api.get_spread(yes_token)
        snapshot["spread"] = float(sd.get("spread", 0)) or None
    except Exception as e:
        snapshot["errors"].append(f"spread: {e}")
        logger.warning(f"  spread failed for {market_id}: {e}")

    # Fetch midpoint separately via /midpoint endpoint
    try:
        mid = api.get_midpoint(yes_token)
        if mid is not None and mid > 0:
            snapshot["midpoint"]  = mid
            snapshot["yes_price"] = mid
            snapshot["no_price"]  = round(1.0 - mid, 6)
    except Exception as e:
        snapshot["errors"].append(f"midpoint: {e}")
        logger.warning(f"  midpoint failed for {market_id}: {e}")

    try:
        fidelity_mins = FIDELITY_MAP.get(PRICE_HISTORY_FIDELITY, 60)
        snapshot["price_history"] = api.get_price_history(
            yes_token, fidelity=fidelity_mins, days=7)
    except Exception as e:
        snapshot["errors"].append(f"price_history: {e}")
        logger.warning(f"  price_history failed for {market_id}: {e}")

    # Fallback: derive price from latest price_history point if midpoint fetch failed
    if snapshot["yes_price"] is None and snapshot["price_history"]:
        try:
            hist = sorted(snapshot["price_history"], key=lambda x: x.get("t", 0))
            latest_p = float(hist[-1].get("p", 0)) if hist else 0.0
            if latest_p > 0:
                snapshot["yes_price"] = latest_p
                snapshot["midpoint"]  = latest_p
                snapshot["no_price"]  = round(1.0 - latest_p, 6)
                snapshot["errors"].append("midpoint: DEGRADED — used price_history fallback, not live orderbook")
        except Exception as e:
            logger.warning(f"  price_history fallback failed for {market_id}: {e}")

    try:
        snapshot["fee_rate_bps"] = api.get_fee_rate(yes_token)
    except Exception as e:
        snapshot["errors"].append(f"fee_rate: {e}")

    try:
        snapshot["open_interest"] = api.get_open_interest(market_id)
    except Exception as e:
        snapshot["errors"].append(f"open_interest: {e}")
        logger.warning(f"  OI failed for {market_id}: {e}")

    try:
        condition_id = market.get("condition_id")
        if condition_id:
            snapshot["top_holders"] = api.get_top_holders(condition_id, limit=10)
    except Exception as e:
        snapshot["errors"].append(f"top_holders: {e}")

    try:
        snapshot["recent_trades"] = api.get_trades(market_id, limit=50)
    except Exception as e:
        snapshot["errors"].append(f"recent_trades: {e}")

    return snapshot


def run():
    logger.info("=== Data collector starting ===")

    watchlist = db.get_watchlist()
    if not watchlist:
        logger.warning("Watchlist is empty — run market_scanner first")
        return {"agent": "data_collector", "collected": 0, "failed": 0}

    collected = 0
    failed    = 0

    for market in watchlist:
        market_id = market.get("market_id", "unknown")
        logger.info(f"Collecting: {market_id} — {str(market.get('question', ''))[:60]}")
        try:
            snapshot = _collect_market_snapshot(market)
            db.insert_snapshot(snapshot)
            if snapshot["errors"]:
                logger.warning(f"  Saved with {len(snapshot['errors'])} partial errors")
            collected += 1
        except Exception as e:
            logger.error(f"  Failed for {market_id}: {e}")
            failed += 1

    logger.info(f"Collection complete: {collected} saved, {failed} failed")
    return {"agent": "data_collector", "collected": collected, "failed": failed}
