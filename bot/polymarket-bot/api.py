# api.py — thin wrapper over all three Polymarket public APIs
# All calls are read-only. No auth required.

import httpx
import logging
from typing import Any

from config import GAMMA_API, CLOB_API, DATA_API

logger = logging.getLogger(__name__)

# Shared client with sensible defaults
_client = httpx.Client(
    timeout=15.0,
    headers={"User-Agent": "polymarket-bot/1.0 (learning bot, read-only)"},
)


def _get(base: str, path: str, params: dict = None) -> Any:
    """Make a GET request and return parsed JSON. Raises on HTTP errors."""
    url = f"{base}{path}"
    try:
        resp = _client.get(url, params=params or {})
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPStatusError as e:
        logger.error(f"HTTP {e.response.status_code} from {url}: {e.response.text[:200]}")
        raise
    except httpx.RequestError as e:
        logger.error(f"Request failed for {url}: {e}")
        raise


# ── Gamma API ─────────────────────────────────────────────────────────────────

def get_events(
    active: bool = True,
    closed: bool = False,
    order: str = "volume24hr",
    ascending: bool = False,
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    """Fetch events (each contains one or more markets)."""
    data = _get(GAMMA_API, "/events", {
        "active": str(active).lower(),
        "closed": str(closed).lower(),
        "order": order,
        "ascending": str(ascending).lower(),
        "limit": limit,
        "offset": offset,
    })
    return data if isinstance(data, list) else data.get("events", [])


def get_event_by_slug(slug: str) -> dict:
    return _get(GAMMA_API, f"/events/slug/{slug}")


def get_markets(
    active: bool = True,
    closed: bool = False,
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    data = _get(GAMMA_API, "/markets", {
        "active": str(active).lower(),
        "closed": str(closed).lower(),
        "limit": limit,
        "offset": offset,
    })
    return data if isinstance(data, list) else data.get("markets", [])


def get_tags() -> list[dict]:
    return _get(GAMMA_API, "/tags")


def get_market_resolution(market_id: str) -> dict | None:
    """
    Fetch a single market's resolution status from the Gamma API.
    Returns the market dict (includes 'closed' and 'resolutionPrice') or None on error.
    """
    try:
        data = _get(GAMMA_API, "/markets", {"id": market_id, "limit": 1})
        markets = data if isinstance(data, list) else data.get("markets", [])
        return markets[0] if markets else None
    except Exception:
        return None


# ── CLOB API ──────────────────────────────────────────────────────────────────

def get_price(token_id: str, side: str = "buy") -> float | None:
    """Get current best price for a token. side = 'buy' or 'sell'."""
    try:
        data = _get(CLOB_API, "/price", {"token_id": token_id, "side": side})
        return float(data.get("price", 0))
    except Exception:
        return None


def get_spread(token_id: str) -> dict:
    """Get spread for a token. Returns {"spread": "value"} only (mid/sell no longer included)."""
    return _get(CLOB_API, "/spread", {"token_id": token_id})


def get_orderbook(token_id: str) -> dict:
    """Get full orderbook for a token. Returns {bids: [...], asks: [...]}."""
    return _get(CLOB_API, "/book", {"token_id": token_id})


def get_price_history(
    token_id: str,
    fidelity: int = 60,   # minutes — 1, 5, 60, 1440
    days: int = 7,
) -> list[dict]:
    """
    Fetch historical prices. Each entry: {t: unix_timestamp, p: price}.
    fidelity in minutes: 1=1m, 5=5m, 60=1h, 1440=1d
    """
    import time
    end_ts = int(time.time())
    start_ts = end_ts - days * 24 * 3600
    raw = _get(CLOB_API, "/prices-history", {
        "market": token_id,
        "startTs": start_ts,
        "endTs": end_ts,
        "fidelity": fidelity,
    })
    # Response is {"history": [...]} or a list directly
    if isinstance(raw, dict):
        return raw.get("history", [])
    return raw if isinstance(raw, list) else []


def get_midpoint(token_id: str) -> float | None:
    """Returns the current YES midpoint or None on any failure."""
    price, _ = get_midpoint_status(token_id)
    return price


def get_midpoint_status(token_id: str) -> tuple[float | None, bool]:
    """
    Returns (midpoint_price, is_gone).

      (price, False) — got the midpoint, price > 0
      (None,  True)  — orderbook returned 404, market is permanently gone
                       (resolved, delisted, or never existed). Callers should
                       cache the token_id and stop querying it.
      (None,  False) — transient failure (timeout, 5xx, parse error). Caller
                       may retry later.

    Bypasses _get so the 404 case does not log at ERROR — that path is
    expected behaviour for resolved markets and was producing log floods
    from outcome_tracker's live-API fallback.
    """
    url = f"{CLOB_API}/midpoint"
    try:
        resp = _client.get(url, params={"token_id": token_id})
    except httpx.RequestError as e:
        logger.warning(f"midpoint transport error for {token_id[:20]}…: {e}")
        return None, False

    if resp.status_code == 404:
        logger.debug(f"No orderbook for token {token_id[:20]}… (resolved/delisted)")
        return None, True

    try:
        resp.raise_for_status()
    except httpx.HTTPStatusError as e:
        logger.warning(f"midpoint HTTP {e.response.status_code} for {token_id[:20]}…: {e.response.text[:120]}")
        return None, False

    try:
        v = float(resp.json().get("mid", 0))
    except (ValueError, TypeError) as e:
        logger.warning(f"midpoint parse error for {token_id[:20]}…: {e}")
        return None, False

    return (v, False) if v > 0 else (None, False)


def get_fee_rate(token_id: str) -> float:
    """Returns the taker fee rate in bps for a token (0 if fee-free)."""
    try:
        data = _get(CLOB_API, "/fee-rate", {"token_id": token_id})
        return float(data.get("fee_rate_bps", 0))
    except Exception:
        return 0.0


# ── Data API ──────────────────────────────────────────────────────────────────

def get_open_interest(market_id: str) -> dict:
    """Get open interest for a market."""
    return _get(DATA_API, "/oi", {"market": market_id})


def get_top_holders(market_id: str, limit: int = 20) -> list[dict]:
    """Get top position holders for a market."""
    return _get(DATA_API, "/holders", {"market": market_id, "limit": limit})


def get_trades(
    market_id: str,
    limit: int = 50,
) -> list[dict]:
    """Get recent trade history for a market."""
    return _get(DATA_API, "/trades", {"market": market_id, "limit": limit})


def get_user_positions(user_address: str) -> list[dict]:
    """
    Get all positions held by a wallet address (proxy/funder for sig_type=1/3,
    or the EOA for sig_type=0).

    Returns the parsed JSON list. Each entry typically includes asset
    (token_id), size (shares held), avgPrice, conditionId, etc.
    Returns [] on any error so callers can soft-fail in reconciliation paths.
    """
    if not user_address:
        return []
    try:
        data = _get(DATA_API, "/positions", {"user": user_address})
        return data if isinstance(data, list) else []
    except Exception as e:
        logger.warning(f"get_user_positions failed for {user_address}: {e}")
        return []
