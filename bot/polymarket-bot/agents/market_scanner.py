# agents/market_scanner.py
# Phase 1: Scan active markets, score them, build a ranked watchlist.
#
# Output: upserted into the markets table in Postgres

import json
import logging
from datetime import datetime, timezone

import api
import db
from config import (
    SCANNER_LIMIT, SCANNER_PAGES,
    MIN_VOLUME_24H, MIN_LIQUIDITY,
    MIN_HOURS_TO_CLOSE, MAX_HOURS_TO_CLOSE, MAX_WATCHLIST_SIZE,
    PRICE_MIN, PRICE_MAX,
    VOLUME_SWEET_SPOT_PEAK, VOLUME_SWEET_SPOT_MAX,
    MICRO_EVENT_KEYWORDS,
)

logger = logging.getLogger(__name__)


def _parse_end_date(end_date_str):
    if not end_date_str:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%d"):
        try:
            return datetime.strptime(end_date_str, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _hours_until_close(end_date_str):
    dt = _parse_end_date(end_date_str)
    if not dt:
        return 9999.0
    delta = dt - datetime.now(timezone.utc)
    return max(delta.total_seconds() / 3600, 0)


def _tag_micro_event(question: str) -> str:
    """
    Return the best-matching micro-event category for a market question,
    or 'other' if nothing matches.
    """
    q = (question or "").lower()
    for category, keywords in MICRO_EVENT_KEYWORDS.items():
        if any(kw in q for kw in keywords):
            return category
    return "other"


def _volume_score(vol: float) -> float:
    """
    Score volume using a sweetspot curve.
    - Peaks at VOLUME_SWEET_SPOT_PEAK
    - Falls linearly above VOLUME_SWEET_SPOT_MAX (whale-distorted mega-markets)
    - Returns 0.0–0.30
    """
    if vol < MIN_VOLUME_24H:
        return 0.0
    if vol <= VOLUME_SWEET_SPOT_PEAK:
        # Ramp up to peak
        return 0.30 * (vol / VOLUME_SWEET_SPOT_PEAK)
    if vol <= VOLUME_SWEET_SPOT_MAX:
        return 0.30
    # Penalise beyond sweetspot max: reduce score linearly
    penalty = min(0.15, 0.15 * ((vol - VOLUME_SWEET_SPOT_MAX) / VOLUME_SWEET_SPOT_MAX))
    return max(0.15, 0.30 - penalty)


def _score_market(event, market):
    score = 0.0
    vol = float(market.get("volume24hr") or market.get("volume") or 0)
    score += _volume_score(vol)
    liq = float(market.get("liquidity") or 0)
    if liq >= MIN_LIQUIDITY:
        score += min(0.20, 0.20 * (liq / 50_000))
    hours = _hours_until_close(market.get("endDate") or event.get("endDate"))
    if MIN_HOURS_TO_CLOSE <= hours <= MAX_HOURS_TO_CLOSE:
        score += 0.20
    try:
        prices = [float(p) for p in json.loads(market.get("outcomePrices") or "[]")]
    except Exception:
        prices = []
    if prices:
        mid = prices[0]
        if PRICE_MIN <= mid <= PRICE_MAX:
            score += 0.15 * (1 - abs(mid - 0.5) / 0.5)
    if event.get("negRisk") or market.get("negRisk"):
        score += 0.15
    if market.get("enableOrderBook"):
        score += 0.10
    return min(score, 1.0)


def _extract_markets_from_events(events):
    results = []
    for event in events:
        for m in (event.get("markets") or []):
            m["_event_title"] = event.get("title")
            m["_event_slug"]  = event.get("slug")
            m["_neg_risk"]    = event.get("negRisk", False)
            m["_tags"]        = [t.get("label", "").lower() for t in (event.get("tags") or [])]
            results.append(m)
    return results


def run():
    logger.info("=== Market scanner starting ===")
    all_markets = []
    for page in range(SCANNER_PAGES):
        offset = page * SCANNER_LIMIT
        try:
            events = api.get_events(active=True, closed=False, order="volume24hr",
                                    ascending=False, limit=SCANNER_LIMIT, offset=offset)
        except Exception as e:
            logger.error(f"Failed to fetch page {page+1}: {e}")
            break
        if not events:
            break
        markets = _extract_markets_from_events(events)
        all_markets.extend(markets)
        logger.info(f"Page {page+1}: {len(events)} events → {len(markets)} markets")

    logger.info(f"Total before filter: {len(all_markets)}")

    filtered = [
        m for m in all_markets
        if m.get("enableOrderBook")
        and float(m.get("volume24hr") or m.get("volume") or 0) >= MIN_VOLUME_24H
        and float(m.get("liquidity") or 0) >= MIN_LIQUIDITY
        and MIN_HOURS_TO_CLOSE <= _hours_until_close(m.get("endDate")) <= MAX_HOURS_TO_CLOSE
    ]
    logger.info(f"After filter: {len(filtered)}")

    scored = sorted(
        [(_score_market({"negRisk": m.get("_neg_risk"), "tags": [{"label": t} for t in m.get("_tags", [])], "endDate": m.get("endDate")}, m), m)
         for m in filtered],
        key=lambda x: x[0], reverse=True
    )[:MAX_WATCHLIST_SIZE]

    watchlist = []
    for score, m in scored:
        try:
            token_ids = json.loads(m.get("clobTokenIds") or "[]")
        except Exception:
            token_ids = []
        try:
            outcomes = json.loads(m.get("outcomes") or "[]")
        except Exception:
            outcomes = []

        question = m.get("question") or m.get("title") or ""
        category = _tag_micro_event(question)

        watchlist.append({
            "market_id":      m.get("id"),
            "condition_id":   m.get("conditionId"),
            "question":       question,
            "event_title":    m.get("_event_title"),
            "event_slug":     m.get("_event_slug"),
            "tags":           m.get("_tags", []),
            "category":       category,
            "neg_risk":       m.get("_neg_risk", False),
            "token_ids":      token_ids,
            "outcomes":       outcomes,
            "volume_24h":     float(m.get("volume24hr") or 0),
            "liquidity":      float(m.get("liquidity") or 0),
            "end_date":       _parse_end_date(m.get("endDate")),
            "hours_to_close": _hours_until_close(m.get("endDate")),
            "fees_enabled":   m.get("feesEnabled", False),
            "score":          round(score, 4),
        })

    upserted = db.upsert_markets(watchlist)
    logger.info(f"Upserted {upserted} markets")

    return {
        "agent": "market_scanner",
        "scanned": len(all_markets),
        "filtered": len(filtered),
        "watchlist_size": len(watchlist),
        "top_market": watchlist[0]["question"] if watchlist else None,
    }
