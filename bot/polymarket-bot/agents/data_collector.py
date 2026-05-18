# agents/data_collector.py
# Phase 2: Collect a full data snapshot for each watched market.
# Output: inserted into the snapshots table in Postgres.
#
# Collection modes:
#   light (every run)  — midpoint, yes/no ask, spread, fee_rate_bps
#   deep  (every Nth)  — + price_history, open_interest, top_holders, recent_trades
#
# DEEP_COLLECTION_INTERVAL controls the cadence. Counter is persisted in
# state/deep_collection_counter.json so it survives restarts.

import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import api
import db
from config import (
    PRICE_HISTORY_FIDELITY,
    PRICE_HISTORY_LIMIT,
    DEEP_COLLECTION_INTERVAL,
    STATE_DIR,
)

logger = logging.getLogger(__name__)
FIDELITY_MAP = {"1m": 1, "5m": 5, "1h": 60, "1d": 1440}

_COUNTER_FILE = os.path.join(STATE_DIR, "deep_collection_counter.json")


def _is_deep_run() -> bool:
    """Increment the persistent run counter and return True on every Nth tick."""
    try:
        with open(_COUNTER_FILE) as f:
            count = json.load(f).get("count", 0)
    except (FileNotFoundError, json.JSONDecodeError):
        count = 0
    count += 1
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(_COUNTER_FILE, "w") as f:
        json.dump({"count": count}, f)
    return count % DEEP_COLLECTION_INTERVAL == 1   # runs 1, 7, 13 … are deep


def _collect_market_snapshot(market, deep: bool = True):
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

    # ── Liveness check — bail early if orderbook is gone (resolved/delisted) ──
    mid, is_gone = api.get_midpoint_status(yes_token)
    if is_gone:
        snapshot["is_gone"] = True
        return snapshot

    if mid is not None and mid > 0:
        snapshot["midpoint"]  = mid
        snapshot["yes_price"] = mid
        snapshot["no_price"]  = round(1.0 - mid, 6)

    # ── Light fields (every run) ──────────────────────────────────────────────

    try:
        yes_ask = api.get_price(yes_token, side="buy")
        if yes_ask is not None and yes_ask > 0:
            snapshot["yes_ask"] = yes_ask
    except Exception as e:
        snapshot["errors"].append(f"yes_ask: {e}")
        logger.warning(f"  yes_ask failed for {market_id}: {e}")

    try:
        if no_token:
            no_ask = api.get_price(no_token, side="buy")
            if no_ask is not None and no_ask > 0:
                snapshot["no_ask"] = no_ask
    except Exception as e:
        snapshot["errors"].append(f"no_ask: {e}")
        logger.warning(f"  no_ask failed for {market_id}: {e}")

    # /spread only returns {"spread": "value"} — no longer includes mid or sell
    # A 404 here means the orderbook is gone even when midpoint is still cached.
    sd, spread_gone = api.get_spread_or_none(yes_token)
    if spread_gone:
        snapshot["is_gone"] = True
        return snapshot
    if sd is not None:
        snapshot["spread"] = float(sd.get("spread", 0)) or None

    try:
        snapshot["fee_rate_bps"] = api.get_fee_rate(yes_token)
    except Exception as e:
        snapshot["errors"].append(f"fee_rate: {e}")

    # ── Deep fields (every Nth run only) ─────────────────────────────────────

    if deep:
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
                    snapshot["errors"].append(
                        "midpoint: DEGRADED — used price_history fallback, not live orderbook"
                    )
            except Exception as e:
                logger.warning(f"  price_history fallback failed for {market_id}: {e}")

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

    is_deep = _is_deep_run()
    logger.info(f"Collection mode: {'DEEP' if is_deep else 'light'}")

    collected = 0
    failed    = 0

    # Parallelise the HTTP collection phase across all markets.
    # httpx.Client is thread-safe for concurrent reads.
    # DB inserts run sequentially in the main thread to stay within the connection pool limit.
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {
            pool.submit(_collect_market_snapshot, market, is_deep): market
            for market in watchlist
        }
        for future in as_completed(futures):
            market    = futures[future]
            market_id = market.get("market_id", "unknown")
            try:
                snapshot = future.result()
                if snapshot.get("is_gone"):
                    logger.info(f"Pruning dead market {market_id} — orderbook gone")
                    db.prune_dead_market(market_id)
                    continue
                logger.info(f"Collected: {market_id} — {str(market.get('question', ''))[:60]}")
                db.insert_snapshot(snapshot)
                if snapshot["errors"]:
                    logger.warning(f"  Saved with {len(snapshot['errors'])} partial errors")
                collected += 1
            except Exception as e:
                logger.error(f"  Failed for {market_id}: {e}")
                failed += 1

    logger.info(f"Collection complete: {collected} saved, {failed} failed")
    return {"agent": "data_collector", "collected": collected, "failed": failed, "deep": is_deep}
