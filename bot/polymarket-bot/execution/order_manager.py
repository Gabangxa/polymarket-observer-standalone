# execution/order_manager.py — CLOB order placement, polling, and cancellation
#
# All prices and sizes use Decimal to avoid float drift.
# API calls retry with exponential backoff (1s → 2s → 4s → ... → 30s cap).
# One failed order never raises out of this module — callers get a status dict.

import logging
import os
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN, ROUND_UP

import alerts
import db
import nats_bus
from config import ORDER_MAX_RETRIES, ORDER_TTL_SPREAD_SECS, ORDER_TTL_NEG_RISK_SECS, ORDER_TTL_TAIL_SECS, ORDER_TTL_EXIT_SECS

logger = logging.getLogger(__name__)

_BACKOFF_BASE   = 1.0
_BACKOFF_CAP    = 30.0
_GTD_BUFFER_SECS = 60   # Polymarket enforces: expiration must be > now + 60s

# Tick size cache: token_id → (Decimal tick, fetched_at unix-secs).
# Polymarket markets use either 0.01 or 0.001; wrong precision → "Invalid order inputs".
# Tail markets (price > 0.96 or < 0.04) use 0.001 tick — exactly the regime
# tail_yield_engine targets, so silently defaulting to 0.01 would leak edge
# (e.g. fair=0.987 → submitted at 0.98) and trigger non-retryable rejections.
# Fail closed instead — callers handle the exception and skip this cycle.
#
# Cache entries expire after _TICK_CACHE_TTL_SECS so that prices crossing the
# 0.96/0.04 boundary (where Polymarket tightens tick from 0.01 to 0.001) get
# re-resolved within a bounded window. The cache hit rate stays high — typical
# strategy cycles touch a token many times per minute — while drift is bounded.
_TICK_CACHE: dict[str, tuple[Decimal, float]] = {}
_TICK_CACHE_TTL_SECS = 60.0


class TickSizeLookupError(RuntimeError):
    """CLOB tick size lookup failed — fail closed rather than guess and risk silent edge loss."""
    pass


def _tick_dec(client, token_id: str) -> Decimal:
    cached = _TICK_CACHE.get(token_id)
    if cached is not None:
        tick, fetched_at = cached
        if time.time() - fetched_at < _TICK_CACHE_TTL_SECS:
            return tick
    try:
        ts = Decimal(str(client.get_tick_size(token_id)))
        _TICK_CACHE[token_id] = (ts, time.time())
        return ts
    except Exception as e:
        raise TickSizeLookupError(
            f"tick size lookup failed for token={token_id[:24]}…: {e}"
        ) from e


def _invalidate_tick(token_id: str) -> None:
    """Drop a tick-cache entry so the next call re-fetches from the CLOB.
    Use after the CLOB rejects an order with an invalid-tick-size error —
    the market's tick has likely tightened or loosened across a price boundary."""
    _TICK_CACHE.pop(token_id, None)


# Static floor (USDC notional) = Polymarket's documented $1 order minimum.
# This is NOT a system-imposed minimum above the exchange's: the authoritative
# per-market floor is the CLOB's `min_order_size` (shares), read live in
# _min_order_shares/_enforce_min_shares at placement. This static value is only
# used where the CLOB cannot be reached: (1) the pre-client skip in
# _size_from_signal (no client available there) so genuine sub-$1 dust never
# gets built into an order the CLOB would reject as non-retryable, and (2) the
# fallback when the per-market min_order_size lookup itself fails.
#
# Kept at $1 (not higher): at a $25 bankroll with MAX_POSITION_PCT=0.10 the
# per-position cap is $2.50, so any floor above that would suppress every
# spread_engine/tail_yield order before it reached the book.
_MIN_ORDER_USDC = Decimal("1.0")

# Min-order-size cache: token_id → (Decimal min_shares, fetched_at unix-secs).
# The CLOB enforces a per-market minimum order size denominated in SHARES,
# exposed on the order book as `min_order_size`. It is the authoritative floor;
# _MIN_ORDER_USDC is only the fallback when the lookup fails. Cached with the
# same TTL as the tick size so churned strategy cycles stay cheap.
_MIN_SIZE_CACHE: dict[str, tuple[Decimal, float]] = {}


def _min_order_shares(client, token_id: str, price: Decimal) -> Decimal:
    """
    Minimum order size in SHARES the CLOB will accept for this token.

    Reads the order book's `min_order_size` (shares). On any failure, falls
    back to deriving the share count from the static _MIN_ORDER_USDC floor at
    the given price, rounded UP so the fallback never lands below a real
    minimum. Never raises — a lookup blip must not block order placement.
    """
    cached = _MIN_SIZE_CACHE.get(token_id)
    if cached is not None:
        shares, fetched_at = cached
        if time.time() - fetched_at < _TICK_CACHE_TTL_SECS:
            return shares

    shares: Decimal
    try:
        ob = client.get_order_book(token_id)
        raw = ob.get("min_order_size") if isinstance(ob, dict) else getattr(ob, "min_order_size", None)
        if raw is None:
            raise ValueError("min_order_size missing from order book")
        shares = Decimal(str(raw))
        if shares <= 0:
            raise ValueError(f"non-positive min_order_size: {raw}")
    except Exception as e:
        fallback = (_MIN_ORDER_USDC / price).quantize(Decimal("0.01"), rounding=ROUND_UP)
        logger.warning(
            f"min_order_size lookup failed for token={token_id[:24]}…: {e} "
            f"— using ${_MIN_ORDER_USDC} fallback = {fallback} shares @ {price}"
        )
        shares = fallback

    _MIN_SIZE_CACHE[token_id] = (shares, time.time())
    return shares


def _enforce_min_shares(client, token_id: str, price: Decimal, size_shares: Decimal) -> Decimal:
    """
    Bump size_shares up to the CLOB's per-market minimum if it falls below.
    Returns the (possibly increased) share count quantized to 2dp. This lets a
    sub-minimum computed size still produce a placeable order rather than a
    non-retryable 'min size' rejection that silently consumes the signal.
    """
    min_shares = _min_order_shares(client, token_id, price)
    if size_shares < min_shares:
        bumped = min_shares.quantize(Decimal("0.01"), rounding=ROUND_UP)
        logger.info(
            f"Order size below CLOB minimum — bumping {size_shares} → {bumped} shares "
            f"| token={token_id[:24]}…"
        )
        return bumped
    return size_shares


def _exceeds_caps(market_id: str, token_id: str, size_usdc: Decimal, side: str = "BUY") -> str | None:
    """
    Authoritative post-bump cap check — the LAST gate that sees an order's
    final USDC notional before it ships to the CLOB.

    pre_trade_gate runs *before* _enforce_min_shares bumps a sub-minimum order
    up to the CLOB's per-market share floor. On a market whose share minimum
    exceeds the cap at the quoted price, that bump can lift notional back above
    the per-position or portfolio cap the gate already cleared. This re-checks
    the *final* notional and tells the caller to skip (not down-size) on a
    breach: the CLOB minimum is a hard floor, so an order that only fits the
    cap below that minimum cannot be placed at all — not trading the market is
    the only disciplined outcome at this bankroll.

    The per-position cap bounds long (BUY) exposure, so it is applied to BUY
    orders only; SELL legs (neg-risk maker) add no long position and are
    checked against the portfolio notional cap alone.

    Returns a human-readable reason string on breach, or None when the order
    is within both caps.
    """
    bankroll = Decimal(str(db.get_bankroll()))
    if bankroll <= 0:
        return "bankroll not set"
    size = Decimal(str(size_usdc))

    if side == "BUY":
        pos_pct = Decimal(str(db.get_max_position_pct()))
        pos_cap = pos_pct * bankroll
        existing = Decimal("0")
        position = db.get_position(market_id, token_id, "YES")
        if position:
            existing = (
                Decimal(str(position["total_bought"])) +
                Decimal(str(position["working_buy"]))
            )
        if existing + size > pos_cap:
            return (
                f"per-position cap: existing {existing:.2f} + order {size:.2f} "
                f"> {pos_cap:.2f} USDC ({pos_pct * 100:.0f}% of {bankroll})"
            )

    port_pct = Decimal(str(db.get_max_portfolio_pct()))
    port_cap = port_pct * bankroll
    total = Decimal(str(db.get_total_open_exposure()))
    if total + size > port_cap:
        return (
            f"portfolio cap: open {total:.2f} + order {size:.2f} "
            f"> {port_cap:.2f} USDC ({port_pct * 100:.0f}% of {bankroll})"
        )
    return None


def _gtd_expiration(strategy: str) -> int:
    """Unix timestamp (seconds) for GTD expiry. Includes Polymarket's 60s minimum buffer."""
    if strategy == "spread_engine":
        ttl = ORDER_TTL_SPREAD_SECS
    elif strategy == "neg_risk_overround":
        ttl = ORDER_TTL_NEG_RISK_SECS
    elif strategy == "exit":
        ttl = ORDER_TTL_EXIT_SECS
    else:
        ttl = ORDER_TTL_TAIL_SECS
    return int(time.time()) + _GTD_BUFFER_SECS + ttl


def _make_clord_id(strategy: str, signal_id: int) -> str:
    """Generate a unique, deterministic client order ID."""
    ts = int(time.time() * 1000)
    nonce = uuid.uuid4().hex[:8]
    return f"{strategy[:8]}_{signal_id}_{ts}_{nonce}"


# Substrings (case-insensitive) on CLOB error messages that indicate a validation
# failure rather than a transient outage. Retrying these wastes backoff time and
# starves later signals (causing the stale-signal cascade observed in production).
_NON_RETRYABLE_ERROR_PATTERNS = (
    "invalid order",        # "Invalid order inputs"
    "invalid price",
    "invalid size",
    "tick size",
    "min size",
    "minimum size",
    "min_order_size",
    "insufficient",         # insufficient balance / allowance
    "not enough balance",
    "not allowed",
    "unauthorized",
    "forbidden",
    "bad request",
    "signature",            # malformed signed order
    "nonce",
    "already exists",
    "duplicate",
    "expired",              # GTD expiration in the past
    "version_mismatch",     # order_version_mismatch — server-side schema rejection, never recovers
    "order_type",           # order-type incompatibility
    "market closed",        # order submitted to a resolved/inactive market
    "market not active",
    "does not exist",       # orderbook/market delisted or resolved — token no longer tradeable
    "not found",            # generic resource-not-found rejections
)


def _is_retryable(exc: Exception) -> bool:
    """
    Return True if this error looks transient (timeout, 5xx, connection),
    False if it's a validation/business-logic rejection that won't recover with retry.
    """
    msg = (_exception_detail(exc) or str(exc)).lower()
    for pattern in _NON_RETRYABLE_ERROR_PATTERNS:
        if pattern in msg:
            return False
    return True


def _exception_detail(exc: Exception) -> str:
    """
    Best-effort extraction of useful info from a py-clob-client PolyApiException.
    Falls back to repr(exc) for non-CLOB exceptions. The exception's str() drops
    the status code and JSON body, which is what we actually need to diagnose
    server-side rejections like 'Invalid order inputs'.
    """
    status = getattr(exc, "status_code", None)
    body   = getattr(exc, "error_msg",   None)
    if status is not None or body is not None:
        return f"HTTP {status} body={body!r}"
    return repr(exc)


def _summarise_order_args(order_args, neg_risk: bool) -> dict:
    """Compact, log-safe snapshot of what we tried to submit."""
    return {
        "token_id":   (getattr(order_args, "token_id", "") or "")[:24] + "…",
        "side":       getattr(order_args, "side", None),
        "price":      getattr(order_args, "price", None),
        "size":       getattr(order_args, "size", None),
        "expiration": getattr(order_args, "expiration", None),
        "neg_risk":   neg_risk,
    }


def _dump_signed_order(signed, opts, context: dict) -> None:
    """Log the full signed EIP-712 payload. Gated on POLYMARKET_DEBUG_SIGN=1.
    Captures verifyingContract, chainId, signatureType, and the full order struct
    so we can diagnose signing mismatches without guessing."""
    if not os.environ.get("POLYMARKET_DEBUG_SIGN"):
        return
    try:
        payload = signed.dict() if hasattr(signed, "dict") else vars(signed)
    except Exception:
        payload = repr(signed)
    logger.info(
        "SIGNED_ORDER_DUMP | context=%s | neg_risk=%s | payload=%r",
        context,
        getattr(opts, "neg_risk", None) if opts else False,
        payload,
    )


def _backoff_retry(fn, max_retries: int = ORDER_MAX_RETRIES):
    """
    Call fn() with exponential backoff on exception.
    Returns the result on success. Raises the last exception after all retries.
    Validation errors (tick size, invalid inputs, insufficient balance, …) are
    raised immediately without retry — these don't recover with time.
    """
    delay = _BACKOFF_BASE
    for attempt in range(1, max_retries + 1):
        try:
            return fn()
        except Exception as e:
            if not _is_retryable(e):
                logger.warning(
                    f"API call rejected (no retry — validation error): {e}"
                )
                raise
            if attempt == max_retries:
                raise
            wait = min(delay, _BACKOFF_CAP)
            logger.warning(
                f"API call failed (attempt {attempt}/{max_retries}): {e}. "
                f"Retrying in {wait:.0f}s."
            )
            time.sleep(wait)
            delay *= 2


def _size_from_signal(signal: dict, side: str) -> Decimal:
    """
    Compute USDC order size from signal metadata.

    Priority:
      1. Kelly fraction from metadata, capped at per_position_pct * bankroll.
      2. No Kelly → default to per_position_pct * bankroll (the full cap).

    Returns 0 when the bankroll is unset, the computed size rounds to zero, or
    the size falls below the static _MIN_ORDER_USDC floor — the caller treats a
    zero size as "skip this signal" rather than submitting dust to the CLOB.
    The per-market CLOB minimum is enforced separately at placement time via
    _enforce_min_shares (it needs the client and price, unavailable here).
    """
    import db as _db
    bankroll = _db.get_bankroll()
    if bankroll <= 0:
        return Decimal("0")
    try:
        position_pct = float(_db.get_max_position_pct())
    except Exception:
        from config import MAX_POSITION_PCT
        position_pct = MAX_POSITION_PCT
    metadata = signal.get("metadata") or {}
    kelly_fraction = metadata.get("kelly_fraction")
    cap = (Decimal(str(bankroll)) * Decimal(str(position_pct))).quantize(
        Decimal("0.01"), rounding=ROUND_DOWN
    )

    if kelly_fraction and float(kelly_fraction) > 0:
        raw  = Decimal(str(bankroll)) * Decimal(str(kelly_fraction))
        size = min(raw, cap).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
    else:
        size = cap

    if size < _MIN_ORDER_USDC:
        return Decimal("0")

    return size


def _get_token_id(signal: dict, side: str) -> str | None:
    """
    Resolve the CLOB token_id for the side we want to trade.
    token_ids[0] = YES token, token_ids[1] = NO token.
    """
    token_ids = signal.get("token_ids") or []
    if not token_ids:
        return None
    if side == "BUY":
        return token_ids[0]   # buying YES
    return token_ids[1] if len(token_ids) > 1 else token_ids[0]


def _rollback_neg_risk_legs(placed_legs, client) -> int:
    """
    Best-effort cancel of legs placed in a neg-risk batch that ended in partial
    failure. Closes the orphan window for OPEN (resting) legs. FILLED legs
    can't be unwound here — they become real positions for the operator to
    manage; cancel_order returns False quickly on "already filled" / "not
    found" (both non-retryable patterns), so this stays bounded.

    placed_legs: iterable of (clord_id, exchange_order_id) tuples.
    Returns the count of legs successfully canceled on the CLOB.
    """
    canceled = 0
    for clord_id, exch_id in placed_legs:
        if not exch_id:
            continue
        if cancel_order(clord_id, exch_id, client):
            canceled += 1
    return canceled


def _place_neg_risk_legs(signal: dict, client) -> dict:
    """
    Place one BUY YES GTD order per outcome leg for a neg_risk_overround TAKER signal.
    MAKER signals (arb_type='maker') are rejected here — SELL NO execution is not yet
    implemented and executing a MAKER signal as BUY YES produces the wrong trade.
    Returns ok=True only if every leg was submitted.

    Atomicity model: signal is marked executed at the END of the loop, after
    all legs are attempted. On partial failure we best-effort cancel placed
    legs to close the orphan window. On crash mid-loop the signal stays
    unexecuted, but pre_trade_gate Gate 4 (order_exists_for_signal) prevents
    a duplicate re-attempt — the signal then ages out as stale within
    MAX_SIGNAL_AGE_SECS, and the reconciler picks up any CLOB orphans.
    """
    signal_id = signal["id"]
    metadata  = signal.get("metadata") or {}

    arb_type = metadata.get("arb_type", "")
    if arb_type == "maker":
        return _place_neg_risk_maker_legs(signal, client)

    outcomes  = metadata.get("outcomes") or []

    if not outcomes:
        return {"ok": False, "clord_id": None, "error": "no outcomes in metadata"}

    market_ids = [o["market_id"] for o in outcomes if o.get("market_id")]
    if not market_ids:
        return {"ok": False, "clord_id": None, "error": "no market_ids in outcomes"}

    token_map = db.get_token_ids_for_markets(market_ids)

    legs_total     = len(market_ids)
    legs_placed    = 0
    first_clord_id = None
    placed_legs    = []  # (clord_id, exchange_order_id) — rollback targets

    for outcome in outcomes:
        market_id = outcome.get("market_id")
        if not market_id:
            continue

        token_ids = token_map.get(market_id) or []
        if not token_ids:
            logger.warning(f"Neg-risk leg skipped — no token_ids | market={market_id}")
            continue

        yes_ask = outcome.get("yes_ask")
        if yes_ask is None:
            logger.warning(f"Neg-risk leg skipped — no yes_ask | market={market_id}")
            continue

        token_id    = token_ids[0]
        try:
            tick = _tick_dec(client, token_id)
        except TickSizeLookupError as e:
            logger.warning(f"Neg-risk leg skipped — {e} | market={market_id}")
            continue
        price       = Decimal(str(yes_ask)).quantize(tick, rounding=ROUND_DOWN)
        if price <= 0:
            logger.warning(f"Neg-risk leg skipped — price rounded to zero (raw={yes_ask}) | market={market_id}")
            continue
        size_usdc   = _MIN_ORDER_USDC
        # Polymarket CLOB enforces 2-decimal share precision regardless of tick size
        # (py-clob-client ROUNDING_CONFIG: size=2 for every tick). Anything finer is
        # silently rejected as "Invalid order inputs".
        size_shares = (size_usdc / price).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
        # Honour the per-market CLOB minimum (shares) and re-derive notional.
        size_shares = _enforce_min_shares(client, token_id, price, size_shares)
        size_usdc   = (price * size_shares).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
        cap_breach  = _exceeds_caps(market_id, token_id, size_usdc, side="BUY")
        if cap_breach:
            logger.warning(
                f"Neg-risk leg skipped — size_above_cap after min-share bump "
                f"| market={market_id} | {cap_breach}"
            )
            continue
        expiration  = _gtd_expiration("neg_risk_overround")
        expiration_dt = datetime.fromtimestamp(expiration, tz=timezone.utc)
        clord_id    = _make_clord_id("negrisk", signal_id)

        try:
            db.insert_order({
                "clord_id":      clord_id,
                "signal_id":     signal_id,
                "market_id":     market_id,
                "token_id":      token_id,
                "side":          "BUY",
                "price":         float(price),
                "size_usdc":     float(size_usdc),
                "strategy":      "neg_risk_overround",
                "expiration_ts": expiration_dt,
                "reprice_of":    None,
            })
        except Exception as e:
            logger.error(f"DB insert failed for neg_risk leg | clord_id={clord_id}: {e}")
            continue

        try:
            from py_clob_client_v2.clob_types import OrderArgs, OrderType, PartialCreateOrderOptions
            order_args = OrderArgs(
                token_id=token_id,
                price=float(price),
                size=float(size_shares),
                side="BUY",
            )
            _opts = PartialCreateOrderOptions(neg_risk=True)

            def _submit():
                order_args.expiration = expiration
                signed = client.create_order(order_args, _opts)
                _dump_signed_order(signed, _opts, {
                    "signal_id": signal_id,
                    "strategy": "neg_risk_overround",
                    "token_id": token_id[:24],
                    "market_id": market_id,
                })
                return client.post_order(signed, OrderType.GTD)

            db.update_order_status(clord_id, "SENT", submitted_at=datetime.now(timezone.utc))
            response          = _backoff_retry(_submit)
            exchange_order_id = response.get("orderID") or response.get("id", "")
            db.update_order_status(
                clord_id, "OPEN",
                exchange_order_id=exchange_order_id,
                working_qty=float(size_shares),
            )
            try:
                db.upsert_position(market_id, token_id, "YES", delta_working_buy=float(size_shares))
            except Exception as _pe:
                logger.error(
                    f"Position update failed (neg-risk leg remains OPEN on CLOB) "
                    f"| clord_id={clord_id} | {_pe}"
                )

            if first_clord_id is None:
                first_clord_id = clord_id
            legs_placed += 1
            placed_legs.append((clord_id, exchange_order_id))
            logger.info(
                f"Neg-risk leg placed | clord_id={clord_id} | "
                f"market={market_id} | price={price} | size_usdc={size_usdc}"
            )

        except Exception as e:
            detail = _exception_detail(e)
            summary = _summarise_order_args(order_args, neg_risk=True)
            logger.error(
                f"Neg-risk leg submission failed | clord_id={clord_id} market={market_id} "
                f"| sent={summary} | error={detail}"
            )
            db.update_order_status(
                clord_id, "REJECTED",
                error_msg=f"{detail} | sent={summary}",
            )

    ok = legs_placed == legs_total
    if legs_placed > 0 and not ok:
        logger.warning(
            f"Neg-risk partial execution: {legs_placed}/{legs_total} legs | signal_id={signal_id} "
            f"— rolling back {len(placed_legs)} placed leg(s)"
        )
        canceled = _rollback_neg_risk_legs(placed_legs, client)
        logger.warning(
            f"Neg-risk rollback complete | signal_id={signal_id} | "
            f"canceled={canceled}/{len(placed_legs)} (uncanceled may be filled positions)"
        )

    # Mark executed at the end — placed legs are anchored in `orders` so
    # pre_trade_gate Gate 4 prevents duplicate runs even before this mark.
    if signal_id:
        db.mark_signal_executed(signal_id)

    return {
        "ok":      ok,
        "clord_id": first_clord_id,
        "error":   None if ok else f"partial: {legs_placed}/{legs_total} legs placed",
    }


def _place_neg_risk_maker_legs(signal: dict, client) -> dict:
    """
    Place one SELL YES GTD limit order per outcome leg for a neg_risk MAKER signal.

    When sum(YES_mids) > 1.02, YES tokens are collectively over-priced.
    Selling YES at mid price across all legs collects sum(YES_mids) > $1 in USDC.
    At resolution exactly one YES leg is claimed for $1 — all others expire worthless.
    Profit = sum(YES_mids) - $1 (locked at fill time, no further directional risk).

    Partial fill risk: if only K < N legs fill, the remaining filled legs carry
    directional exposure. This is why TTL is short (NEG_RISK_SECS = 2 min).

    Atomicity model: signal is marked executed at the END of the loop. On
    partial failure, placed legs are best-effort cancelled to close the
    orphan window. On crash mid-loop, pre_trade_gate Gate 4
    (order_exists_for_signal) prevents duplicate execution until the signal
    ages out as stale; the reconciler then picks up any CLOB orphans.
    """
    signal_id = signal["id"]
    metadata  = signal.get("metadata") or {}
    outcomes  = metadata.get("outcomes") or []

    if not outcomes:
        return {"ok": False, "clord_id": None, "error": "no outcomes in metadata"}

    legs_total     = len(outcomes)
    legs_placed    = 0
    first_clord_id = None
    placed_legs    = []  # (clord_id, exchange_order_id) — rollback targets

    for outcome in outcomes:
        market_id    = outcome.get("market_id")
        yes_token_id = outcome.get("yes_token_id")
        yes_price    = outcome.get("yes_price")

        if not market_id or not yes_token_id:
            logger.warning(
                f"Maker leg skipped — missing market_id or yes_token_id | market={market_id}"
            )
            continue

        if yes_price is None:
            logger.warning(f"Maker leg skipped — no yes_price | market={market_id}")
            continue

        try:
            tick = _tick_dec(client, yes_token_id)
        except TickSizeLookupError as e:
            logger.warning(f"Maker leg skipped — {e} | market={market_id}")
            continue
        price = Decimal(str(yes_price)).quantize(tick, rounding=ROUND_DOWN)
        if price <= 0:
            logger.warning(f"Maker leg skipped — price rounded to zero (raw={yes_price}) | market={market_id}")
            continue

        size_usdc   = _MIN_ORDER_USDC
        size_shares = (size_usdc / price).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
        # Honour the per-market CLOB minimum (shares) and re-derive notional.
        size_shares = _enforce_min_shares(client, yes_token_id, price, size_shares)
        size_usdc   = (price * size_shares).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
        cap_breach  = _exceeds_caps(market_id, yes_token_id, size_usdc, side="SELL")
        if cap_breach:
            logger.warning(
                f"Maker leg skipped — size_above_cap after min-share bump "
                f"| market={market_id} | {cap_breach}"
            )
            continue
        expiration  = _gtd_expiration("neg_risk_overround")
        expiration_dt = datetime.fromtimestamp(expiration, tz=timezone.utc)
        clord_id    = _make_clord_id("negrsk_m", signal_id)

        try:
            db.insert_order({
                "clord_id":      clord_id,
                "signal_id":     signal_id,
                "market_id":     market_id,
                "token_id":      yes_token_id,
                "side":          "SELL",
                "price":         float(price),
                "size_usdc":     float(size_usdc),
                "strategy":      "neg_risk_overround",
                "expiration_ts": expiration_dt,
                "reprice_of":    None,
            })
        except Exception as e:
            logger.error(f"DB insert failed for maker leg | clord_id={clord_id}: {e}")
            continue

        # Mark working_sell before touching the CLOB
        try:
            db.upsert_position(market_id, yes_token_id, "YES", delta_working_sell=float(size_shares))
        except Exception as _pe:
            logger.error(
                f"Position pre-update failed (maker leg) | clord_id={clord_id} | {_pe}"
            )

        try:
            from py_clob_client_v2.clob_types import OrderArgs, OrderType, PartialCreateOrderOptions
            order_args = OrderArgs(
                token_id=yes_token_id,
                price=float(price),
                size=float(size_shares),
                side="SELL",
            )
            _opts = PartialCreateOrderOptions(neg_risk=True)

            def _submit():
                order_args.expiration = expiration
                signed = client.create_order(order_args, _opts)
                _dump_signed_order(signed, _opts, {
                    "signal_id": signal_id,
                    "strategy": "neg_risk_overround_maker",
                    "token_id": yes_token_id[:24],
                    "market_id": market_id,
                })
                return client.post_order(signed, OrderType.GTD)

            db.update_order_status(clord_id, "SENT", submitted_at=datetime.now(timezone.utc))
            response          = _backoff_retry(_submit)
            exchange_order_id = response.get("orderID") or response.get("id", "")
            db.update_order_status(
                clord_id, "OPEN",
                exchange_order_id=exchange_order_id,
                working_qty=float(size_shares),
            )

            if first_clord_id is None:
                first_clord_id = clord_id
            legs_placed += 1
            placed_legs.append((clord_id, exchange_order_id))
            logger.info(
                f"Maker leg placed | clord_id={clord_id} | market={market_id} "
                f"| price={price} | shares={size_shares}"
            )

        except Exception as e:
            detail = _exception_detail(e)
            summary = _summarise_order_args(order_args, neg_risk=True)
            logger.error(
                f"Maker leg submission failed | clord_id={clord_id} market={market_id} "
                f"| sent={summary} | error={detail}"
            )
            db.update_order_status(
                clord_id, "REJECTED",
                error_msg=f"{detail} | sent={summary}",
            )
            try:
                db.upsert_position(market_id, yes_token_id, "YES", delta_working_sell=-float(size_shares))
            except Exception as _pe:
                logger.error(f"Position rollback failed | clord_id={clord_id} | {_pe}")

    ok = legs_placed == legs_total
    if legs_placed > 0 and not ok:
        logger.warning(
            f"Maker partial execution: {legs_placed}/{legs_total} legs | signal_id={signal_id} "
            f"— rolling back {len(placed_legs)} placed leg(s)"
        )
        canceled = _rollback_neg_risk_legs(placed_legs, client)
        logger.warning(
            f"Maker rollback complete | signal_id={signal_id} | "
            f"canceled={canceled}/{len(placed_legs)} (uncanceled may be filled positions)"
        )

    if signal_id:
        db.mark_signal_executed(signal_id)

    return {
        "ok":      ok,
        "clord_id": first_clord_id,
        "error":   None if ok else f"partial: {legs_placed}/{legs_total} maker legs placed",
    }


def place_order(signal: dict, client, reprice_of: int = None) -> dict:
    """
    Place a GTD CLOB order for the given signal.
    Returns a status dict: {"ok": bool, "clord_id": str, "error": str|None}

    reprice_of: orders.id of the expired order this reprices (None for original orders).

    Strategy routing:
      spread_engine     → LIMIT BUY YES, GTD 10 min
      tail_yield_engine → LIMIT BUY YES, GTD 60 min
    """
    signal_id = signal["id"]
    strategy  = signal["strategy"]
    market_id = signal.get("market_id", "")
    metadata  = signal.get("metadata") or {}

    # Neg-risk is multi-leg — each outcome market gets its own order
    if strategy == "neg_risk_overround":
        return _place_neg_risk_legs(signal, client)

    token_id = _get_token_id(signal, "BUY")
    if not token_id:
        logger.error(f"No token_id for signal {signal_id} market {market_id}")
        return {"ok": False, "clord_id": None, "error": "missing token_id"}

    clord_id = _make_clord_id(strategy, signal_id)

    # Determine price by strategy (both use GTD LIMIT BUY)
    try:
        tick = _tick_dec(client, token_id)
    except TickSizeLookupError as e:
        # Don't mark signal executed — tick failure is potentially transient
        # (CLOB network blip). Next executor cycle will retry.
        logger.warning(f"Skipping signal {signal_id} | {e}")
        return {"ok": False, "clord_id": clord_id, "error": str(e)}
    if strategy == "spread_engine":
        # Post passive maker BUY at one tick below the current ask. This joins
        # the top of the book ahead of any existing bid below the ask, improving
        # fill probability vs. posting at midpoint (which rarely fills).
        # Fall back to midpoint if yes_ask is unavailable.
        raw_ask = metadata.get("yes_ask")
        if raw_ask is not None and float(raw_ask) > 0:
            ask_q = Decimal(str(raw_ask)).quantize(tick, rounding=ROUND_DOWN)
            price = ask_q - tick
            if price <= 0:
                return {"ok": False, "clord_id": clord_id, "error": f"ask-minus-tick non-positive (ask={raw_ask}, tick={tick})"}
        else:
            raw_price = metadata.get("yes_price")
            if raw_price is None:
                return {"ok": False, "clord_id": clord_id, "error": "missing yes_ask and yes_price in metadata"}
            price = Decimal(str(raw_price)).quantize(tick, rounding=ROUND_DOWN)

    elif strategy == "tail_yield_engine":
        raw_price = metadata.get("yes_price")
        if raw_price is None:
            return {"ok": False, "clord_id": clord_id, "error": "missing yes_price in metadata"}
        price = Decimal(str(raw_price)).quantize(tick, rounding=ROUND_DOWN)

    else:
        return {"ok": False, "clord_id": clord_id, "error": f"no order logic for strategy '{strategy}'"}

    if price <= 0:
        return {"ok": False, "clord_id": clord_id, "error": f"price rounded to zero (raw={raw_price})"}

    size_usdc  = _size_from_signal(signal, "BUY")
    if size_usdc <= 0:
        return {"ok": False, "clord_id": clord_id, "error": "computed size is zero"}

    # Compute share quantity once in Decimal to avoid float drift across all three uses below.
    # 2-decimal precision is mandatory — py-clob-client ROUNDING_CONFIG uses size=2 for every
    # tick size, and the CLOB rejects anything finer as "Invalid order inputs".
    size_shares = (size_usdc / price).quantize(Decimal("0.01"), rounding=ROUND_DOWN)

    # Enforce the per-market CLOB minimum order size. If the computed share count
    # is below it, bump up so the order is placeable rather than non-retryably
    # rejected; recompute size_usdc so the DB exposure record matches what ships.
    size_shares = _enforce_min_shares(client, token_id, price, size_shares)
    size_usdc   = (price * size_shares).quantize(Decimal("0.01"), rounding=ROUND_DOWN)

    # Authoritative cap re-check AFTER the min-share bump. pre_trade_gate ran on
    # the pre-bump size; _enforce_min_shares can lift notional above the cap on
    # markets whose share minimum exceeds the cap at this price. Skip rather than
    # ship an over-cap order — the CLOB minimum is a hard floor, so a market that
    # can't be sized within the cap is simply not tradeable at this bankroll.
    cap_breach = _exceeds_caps(market_id, token_id, size_usdc, side="BUY")
    if cap_breach:
        logger.warning(
            f"Order skipped — size_above_cap after min-share bump | "
            f"clord_id={clord_id} strategy={strategy} market={market_id} | {cap_breach}"
        )
        return {"ok": False, "clord_id": clord_id, "error": f"size_above_cap: {cap_breach}"}

    expiration    = _gtd_expiration(strategy)
    expiration_dt = datetime.fromtimestamp(expiration, tz=timezone.utc)

    # Record order in DB before touching the API (idempotency anchor)
    try:
        db.insert_order({
            "clord_id":      clord_id,
            "signal_id":     signal_id,
            "market_id":     market_id,
            "token_id":      token_id,
            "side":          "BUY",
            "price":         float(price),
            "size_usdc":     float(size_usdc),
            "strategy":      strategy,
            "expiration_ts": expiration_dt,
            "reprice_of":    reprice_of,
        })
        # Only mark the signal executed for original orders (not reprices)
        if reprice_of is None and signal_id:
            db.mark_signal_executed(signal_id)
    except Exception as e:
        logger.error(f"DB insert failed for clord_id={clord_id}: {e}")
        return {"ok": False, "clord_id": clord_id, "error": f"db error: {e}"}

    # Submit to CLOB with GTD and backoff
    try:
        from py_clob_client_v2.clob_types import OrderArgs, OrderType, PartialCreateOrderOptions
        order_args = OrderArgs(
            token_id=token_id,
            price=float(price),
            size=float(size_shares),
            side="BUY",
        )
        _opts = PartialCreateOrderOptions(neg_risk=True) if signal.get("neg_risk") else None

        def _submit():
            order_args.expiration = expiration
            signed = client.create_order(order_args, _opts)
            _dump_signed_order(signed, _opts, {
                "signal_id": signal_id,
                "strategy": strategy,
                "token_id": token_id[:24],
                "market_id": market_id,
            })
            return client.post_order(signed, OrderType.GTD)

        db.update_order_status(clord_id, "SENT", submitted_at=datetime.now(timezone.utc))
        response = _backoff_retry(_submit)

        exchange_order_id = response.get("orderID") or response.get("id", "")
        db.update_order_status(
            clord_id, "OPEN",
            exchange_order_id=exchange_order_id,
            working_qty=float(size_shares),
        )
        # Record working qty in positions table. A failure here must NOT mark the
        # order as REJECTED — the order is live on the CLOB. Log and continue;
        # reconcile_positions() will detect and alert on the position drift.
        try:
            db.upsert_position(
                market_id, token_id, "YES",
                delta_working_buy=float(size_shares),
            )
        except Exception as _pe:
            logger.error(
                f"Position update failed (order remains OPEN on CLOB) | "
                f"clord_id={clord_id} | {_pe}"
            )

        logger.info(
            f"Order placed | clord_id={clord_id} | exchange_id={exchange_order_id} | "
            f"strategy={strategy} | price={price} | size_usdc={size_usdc}"
        )
        alerts.order_placed(
            strategy=strategy,
            market_id=market_id,
            question=metadata.get("question", ""),
            clord_id=clord_id,
            price=float(price),
            size_usdc=float(size_usdc),
        )
        nats_bus.publish(
            f"pm.execution.placed.{strategy}.{market_id}",
            {
                "clord_id":          clord_id,
                "exchange_order_id": exchange_order_id,
                "strategy":          strategy,
                "market_id":         market_id,
                "price":             float(price),
                "size_usdc":         float(size_usdc),
                "ts":                datetime.now(timezone.utc).isoformat(),
            },
        )
        return {"ok": True, "clord_id": clord_id, "error": None}

    except Exception as e:
        detail = _exception_detail(e)
        summary = _summarise_order_args(order_args, neg_risk=bool(_opts and _opts.neg_risk))
        logger.error(
            f"Order submission failed | clord_id={clord_id} | strategy={strategy} "
            f"| sent={summary} | error={detail}"
        )
        db.update_order_status(
            clord_id, "REJECTED",
            error_msg=f"{detail} | sent={summary}",
        )
        alerts.order_rejected(
            strategy=strategy,
            market_id=market_id,
            question=metadata.get("question", ""),
            clord_id=clord_id,
            error=detail,
        )
        nats_bus.publish(
            f"pm.execution.rejected.{strategy}.{market_id}",
            {
                "clord_id":  clord_id,
                "strategy":  strategy,
                "market_id": market_id,
                "error":     detail,
                "ts":        datetime.now(timezone.utc).isoformat(),
            },
        )
        # "alerted" tells the executor an order_rejected alert already fired for
        # this failure, so it won't also emit an order_skipped alert.
        return {"ok": False, "clord_id": clord_id, "error": detail, "alerted": True}


def poll_order_status(order: dict, client) -> None:
    """
    Fetch the current fill status of an open order from the CLOB
    and update the orders + positions tables accordingly.
    """
    clord_id          = order["clord_id"]
    exchange_order_id = order.get("exchange_order_id")
    market_id         = order["market_id"]
    token_id          = order["token_id"]
    prev_filled       = Decimal(str(order.get("filled_qty") or 0))
    prev_working      = Decimal(str(order.get("working_qty") or 0))

    if not exchange_order_id:
        return

    try:
        def _fetch():
            return client.get_order(exchange_order_id)

        data = _backoff_retry(_fetch)
    except Exception as e:
        logger.warning(f"poll_order_status failed for clord_id={clord_id}: {e}")
        return

    raw_status    = (data.get("status") or "").upper()
    filled_qty    = Decimal(str(data.get("size_matched") or 0))
    remaining_qty = Decimal(str(data.get("size_remaining") or 0))
    fill_price    = data.get("average_price")

    if raw_status in ("MATCHED", "FILLED") or remaining_qty == 0:
        new_status = "FILLED"
    elif filled_qty > 0:
        new_status = "PARTIALLY_FILLED"
    elif raw_status == "CANCELED":
        new_status = "CANCELED"
    else:
        new_status = "OPEN"

    if new_status == order["status"] and filled_qty == prev_filled:
        return   # nothing changed

    db.update_order_status(
        clord_id,
        new_status,
        filled_qty=float(filled_qty),
        working_qty=float(remaining_qty),
        fill_price=float(fill_price) if fill_price else None,
        filled_at=datetime.now(timezone.utc) if new_status == "FILLED" else None,
        canceled_at=datetime.now(timezone.utc) if new_status == "CANCELED" else None,
    )

    # Update positions based on order direction.
    # BUY fills: increase total_bought, update VWAP avg_cost (no realized PnL yet).
    # SELL fills: increase total_sold, lock realized PnL = (fill - avg_cost) × sold.
    # Both are computed atomically inside upsert_position — no separate read needed.
    delta_fill    = filled_qty - prev_filled
    delta_working = remaining_qty - prev_working
    order_side    = order.get("side", "BUY")
    pos_side      = "YES"   # bot currently only trades YES-outcome tokens
    if delta_fill != 0 or delta_working != 0:
        if order_side == "BUY":
            db.upsert_position(
                market_id, token_id, pos_side,
                delta_bought=float(delta_fill),
                delta_working_buy=float(delta_working),
                avg_cost=float(fill_price) if fill_price else None,
            )
        else:
            db.upsert_position(
                market_id, token_id, pos_side,
                delta_sold=float(delta_fill),
                delta_working_sell=float(delta_working),
                avg_cost=float(fill_price) if fill_price else None,
            )

    if new_status == "FILLED" and fill_price:
        strategy = order.get("strategy", "")
        alerts.order_filled(
            strategy=strategy,
            market_id=market_id,
            clord_id=clord_id,
            filled_qty=float(filled_qty),
            fill_price=float(fill_price),
        )
        nats_bus.publish(
            f"pm.execution.filled.{strategy}.{market_id}",
            {
                "clord_id":   clord_id,
                "strategy":   strategy,
                "market_id":  market_id,
                "filled_qty": float(filled_qty),
                "fill_price": float(fill_price),
                "ts":         datetime.now(timezone.utc).isoformat(),
            },
        )

    logger.info(
        f"Order update | clord_id={clord_id} | status={new_status} | "
        f"filled={filled_qty} | remaining={remaining_qty}"
    )


def cancel_order(clord_id: str, exchange_order_id: str, client) -> bool:
    """Cancel an open order. Returns True on success."""
    try:
        from py_clob_client_v2.clob_types import OrderPayload

        def _cancel():
            return client.cancel_order(OrderPayload(orderID=exchange_order_id))
        _backoff_retry(_cancel)
        db.update_order_status(
            clord_id, "CANCELED",
            canceled_at=datetime.now(timezone.utc),
        )
        logger.info(f"Order canceled | clord_id={clord_id}")
        return True
    except Exception as e:
        logger.error(f"Cancel failed for clord_id={clord_id}: {e}")
        return False


def place_exit_order(position: dict, price: float, client) -> dict:
    """
    Place a SELL GTD order to close an open position at the given price.

    position dict must contain: market_id, token_id, net_shares, strategy.
    price is the current yes_price from the snapshot that triggered the exit.

    The order is recorded with strategy='exit_{original_strategy}' so it is
    distinguishable in the blotter and excluded from strategy-seeding queries.
    Returns {"ok": bool, "clord_id": str, "error": str|None}.
    """
    market_id  = position["market_id"]
    token_id   = position["token_id"]
    net_shares = Decimal(str(position["net_shares"]))
    orig_strat = position.get("strategy", "unknown")
    strategy   = f"exit_{orig_strat}"

    if net_shares <= 0:
        return {"ok": False, "clord_id": None, "error": "zero net shares — nothing to exit"}

    try:
        tick = _tick_dec(client, token_id)
    except TickSizeLookupError as e:
        logger.warning(f"Exit skipped — {e} | market={market_id}")
        return {"ok": False, "clord_id": None, "error": str(e)}
    price_d      = Decimal(str(price)).quantize(tick, rounding=ROUND_DOWN)
    size_shares  = net_shares.quantize(Decimal("0.01"), rounding=ROUND_DOWN)
    size_usdc    = (price_d * size_shares).quantize(Decimal("0.01"), rounding=ROUND_DOWN)

    # Use signal_id=0 sentinel — exits have no originating signal
    clord_id      = _make_clord_id("exit", 0)
    expiration    = _gtd_expiration("exit")
    expiration_dt = datetime.fromtimestamp(expiration, tz=timezone.utc)

    try:
        db.insert_order({
            "clord_id":      clord_id,
            "signal_id":     None,
            "market_id":     market_id,
            "token_id":      token_id,
            "side":          "SELL",
            "price":         float(price_d),
            "size_usdc":     float(size_usdc),
            "strategy":      strategy,
            "expiration_ts": expiration_dt,
            "reprice_of":    None,
        })
    except Exception as e:
        logger.error(f"DB insert failed for exit clord_id={clord_id}: {e}")
        return {"ok": False, "clord_id": clord_id, "error": f"db error: {e}"}

    # Record working sell quantity before touching the CLOB
    try:
        db.upsert_position(market_id, token_id, "YES", delta_working_sell=float(size_shares))
    except Exception as _pe:
        logger.error(f"Position pre-update failed (exit order) | clord_id={clord_id} | {_pe}")

    try:
        from py_clob_client_v2.clob_types import OrderArgs, OrderType, PartialCreateOrderOptions
        order_args = OrderArgs(
            token_id=token_id,
            price=float(price_d),
            size=float(size_shares),
            side="SELL",
        )
        _opts = PartialCreateOrderOptions(neg_risk=True) if position.get("neg_risk") else None

        def _submit():
            order_args.expiration = expiration
            signed = client.create_order(order_args, _opts)
            return client.post_order(signed, OrderType.GTD)

        db.update_order_status(clord_id, "SENT", submitted_at=datetime.now(timezone.utc))
        response = _backoff_retry(_submit)

        exchange_order_id = response.get("orderID") or response.get("id", "")
        db.update_order_status(
            clord_id, "OPEN",
            exchange_order_id=exchange_order_id,
            working_qty=float(size_shares),
        )

        logger.info(
            f"Exit order placed | clord_id={clord_id} | exchange_id={exchange_order_id} "
            f"| strategy={orig_strat} | market={market_id} "
            f"| price={price_d} | shares={size_shares}"
        )
        nats_bus.publish(
            f"pm.execution.exit.{orig_strat}.{market_id}",
            {
                "clord_id":          clord_id,
                "exchange_order_id": exchange_order_id,
                "strategy":          orig_strat,
                "market_id":         market_id,
                "price":             float(price_d),
                "size_usdc":         float(size_usdc),
                "ts":                datetime.now(timezone.utc).isoformat(),
            },
        )
        return {"ok": True, "clord_id": clord_id, "error": None}

    except Exception as e:
        detail = _exception_detail(e)
        summary = _summarise_order_args(order_args, neg_risk=bool(_opts and _opts.neg_risk))
        logger.error(
            f"Exit order submission failed | clord_id={clord_id} "
            f"| sent={summary} | error={detail}"
        )
        db.update_order_status(
            clord_id, "REJECTED",
            error_msg=f"{detail} | sent={summary}",
        )
        # Undo working_sell delta so position state stays accurate
        try:
            db.upsert_position(market_id, token_id, "YES", delta_working_sell=-float(size_shares))
        except Exception as _pe:
            logger.error(f"Position rollback failed (exit order) | clord_id={clord_id} | {_pe}")
        return {"ok": False, "clord_id": clord_id, "error": detail}


def cancel_all_open_orders(client) -> dict:
    """
    Cancel every non-terminal order. This is the kill switch — DB state must
    never claim an order is canceled while it is still live on the CLOB.

    Uses client.cancel_all() for CLOB orders (one API call). A DB row is moved
    to CANCELED only when:
      • it never reached the CLOB (no exchange_order_id) — safe to close, or
      • the CLOB cancel_all() call succeeded — the order is confirmed gone.
    If cancel_all() fails, CLOB-backed orders are LEFT OPEN so the reconciler
    (and a retried kill switch) can still act on them. Marking them CANCELED
    here would hide live orders from reconciliation — the exact failure mode
    this kill switch exists to prevent.

    Returns a summary dict: {attempted, succeeded, failed, db_only, clob_ok}.
    """
    open_orders = db.get_open_orders()
    attempted   = len(open_orders)
    db_only     = sum(1 for o in open_orders if not o.get("exchange_order_id"))
    clob_count  = attempted - db_only

    # Single API call cancels everything on the CLOB.
    clob_ok = True
    if clob_count > 0:
        try:
            def _cancel_all():
                return client.cancel_all()
            _backoff_retry(_cancel_all)
        except Exception as e:
            clob_ok = False
            logger.error(f"cancel_all CLOB call failed: {e}")

    now      = datetime.now(timezone.utc)
    canceled = 0
    for order in open_orders:
        has_eoi = bool(order.get("exchange_order_id"))
        # DB-only orders are always safe to close. CLOB-backed orders only when
        # the cancel call succeeded — otherwise leave them OPEN for reconciliation.
        if not has_eoi or clob_ok:
            db.update_order_status(order["clord_id"], "CANCELED", canceled_at=now)
            canceled += 1

    clob_failed = 0 if clob_ok else clob_count
    summary = {
        "attempted": attempted,
        "succeeded": canceled,
        "failed":    clob_failed,
        "db_only":   db_only,
        "clob_ok":   clob_ok,
    }
    if not clob_ok:
        logger.error(
            f"cancel_all_open_orders: CLOB cancel FAILED — {clob_count} order(s) "
            f"left OPEN in DB and live on the CLOB; reconciler/retry must clear them"
        )
    logger.warning(
        f"cancel_all_open_orders | attempted={attempted} "
        f"succeeded={canceled} failed={clob_failed} db_only={db_only} clob_ok={clob_ok}"
    )
    alerts.cancel_all_fired(summary)
    return summary
