# execution/order_manager.py — CLOB order placement, polling, and cancellation
#
# All prices and sizes use Decimal to avoid float drift.
# API calls retry with exponential backoff (1s → 2s → 4s → ... → 30s cap).
# One failed order never raises out of this module — callers get a status dict.

import logging
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN

import alerts
import db
import nats_bus
from config import ORDER_MAX_RETRIES, ORDER_TTL_SPREAD_SECS, ORDER_TTL_NEG_RISK_SECS, ORDER_TTL_TAIL_SECS, ORDER_TTL_EXIT_SECS

logger = logging.getLogger(__name__)

_BACKOFF_BASE   = 1.0
_BACKOFF_CAP    = 30.0
_GTD_BUFFER_SECS = 60   # Polymarket enforces: expiration must be > now + 60s

# Minimum order size enforced by Polymarket (in USDC)
_MIN_ORDER_USDC = Decimal("5.0")


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


def _backoff_retry(fn, max_retries: int = ORDER_MAX_RETRIES):
    """
    Call fn() with exponential backoff on exception.
    Returns the result on success. Raises the last exception after all retries.
    """
    delay = _BACKOFF_BASE
    for attempt in range(1, max_retries + 1):
        try:
            return fn()
        except Exception as e:
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
    Extract Kelly-sized USDC amount from signal metadata.
    Falls back to MIN_ORDER_USDC if metadata is absent or invalid.
    """
    import db as _db
    from config import MAX_POSITION_PCT
    bankroll = _db.get_bankroll()
    metadata = signal.get("metadata") or {}
    kelly_fraction = metadata.get("kelly_fraction")

    if kelly_fraction and float(kelly_fraction) > 0:
        raw = Decimal(str(bankroll)) * Decimal(str(kelly_fraction))
        cap = Decimal(str(bankroll)) * Decimal(str(MAX_POSITION_PCT))
        size = min(raw, cap).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
    else:
        size = _MIN_ORDER_USDC

    if size < _MIN_ORDER_USDC:
        size = _MIN_ORDER_USDC

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


def _place_neg_risk_legs(signal: dict, client) -> dict:
    """
    Place one BUY YES GTD order per outcome leg for a neg_risk_overround TAKER signal.
    MAKER signals (arb_type='maker') are rejected here — SELL NO execution is not yet
    implemented and executing a MAKER signal as BUY YES produces the wrong trade.
    Returns ok=True only if every leg was submitted.
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

    # Mark executed before touching the API — prevents duplicate legs on retry
    if signal_id:
        db.mark_signal_executed(signal_id)

    legs_total     = len(market_ids)
    legs_placed    = 0
    first_clord_id = None

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
        price       = Decimal(str(yes_ask)).quantize(Decimal("0.001"), rounding=ROUND_DOWN)
        size_usdc   = _MIN_ORDER_USDC
        size_shares = (size_usdc / price).quantize(Decimal("0.0001"), rounding=ROUND_DOWN)
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
            from py_clob_client.clob_types import OrderArgs, OrderType, PartialCreateOrderOptions
            order_args = OrderArgs(
                token_id=token_id,
                price=float(price),
                size=float(size_shares),
                side="BUY",
            )
            _opts = PartialCreateOrderOptions(neg_risk=True)

            def _submit():
                order_args.expiration = str(expiration)
                signed = client.create_order(order_args, _opts)
                return client.post_order(signed, OrderType.GTD)

            db.update_order_status(clord_id, "SENT", submitted_at=datetime.now(timezone.utc))
            response          = _backoff_retry(_submit)
            exchange_order_id = response.get("orderID") or response.get("id", "")
            db.update_order_status(
                clord_id, "OPEN",
                exchange_order_id=exchange_order_id,
                working_qty=float(size_shares),
            )
            db.upsert_position(market_id, token_id, "YES", delta_working_buy=float(size_shares))

            if first_clord_id is None:
                first_clord_id = clord_id
            legs_placed += 1
            logger.info(
                f"Neg-risk leg placed | clord_id={clord_id} | "
                f"market={market_id} | price={price} | size_usdc={size_usdc}"
            )

        except Exception as e:
            logger.error(f"Neg-risk leg submission failed | clord_id={clord_id} market={market_id}: {e}")
            db.update_order_status(clord_id, "REJECTED", error_msg=str(e))

    ok = legs_placed == legs_total
    if legs_placed > 0 and not ok:
        logger.warning(
            f"Neg-risk partial execution: {legs_placed}/{legs_total} legs | signal_id={signal_id}"
        )

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

    All legs are marked executed before the first CLOB submission to prevent
    duplicate execution on retry.
    """
    signal_id = signal["id"]
    metadata  = signal.get("metadata") or {}
    outcomes  = metadata.get("outcomes") or []

    if not outcomes:
        return {"ok": False, "clord_id": None, "error": "no outcomes in metadata"}

    if signal_id:
        db.mark_signal_executed(signal_id)

    legs_total     = len(outcomes)
    legs_placed    = 0
    first_clord_id = None

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

        price       = Decimal(str(yes_price)).quantize(Decimal("0.001"), rounding=ROUND_DOWN)
        size_usdc   = _MIN_ORDER_USDC
        size_shares = (size_usdc / price).quantize(Decimal("0.0001"), rounding=ROUND_DOWN)
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
        db.upsert_position(market_id, yes_token_id, "YES", delta_working_sell=float(size_shares))

        try:
            from py_clob_client.clob_types import OrderArgs, OrderType, PartialCreateOrderOptions
            order_args = OrderArgs(
                token_id=yes_token_id,
                price=float(price),
                size=float(size_shares),
                side="SELL",
            )
            _opts = PartialCreateOrderOptions(neg_risk=True)

            def _submit():
                order_args.expiration = str(expiration)
                signed = client.create_order(order_args, _opts)
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
            logger.info(
                f"Maker leg placed | clord_id={clord_id} | market={market_id} "
                f"| price={price} | shares={size_shares}"
            )

        except Exception as e:
            logger.error(f"Maker leg submission failed | clord_id={clord_id} market={market_id}: {e}")
            db.update_order_status(clord_id, "REJECTED", error_msg=str(e))
            db.upsert_position(market_id, yes_token_id, "YES", delta_working_sell=-float(size_shares))

    ok = legs_placed == legs_total
    if legs_placed > 0 and not ok:
        logger.warning(
            f"Maker partial execution: {legs_placed}/{legs_total} legs | signal_id={signal_id}"
        )

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
    if strategy == "spread_engine":
        raw_price = metadata.get("yes_price")
        if raw_price is None:
            return {"ok": False, "clord_id": clord_id, "error": "missing yes_price in metadata"}
        price = Decimal(str(raw_price)).quantize(Decimal("0.001"), rounding=ROUND_DOWN)

    elif strategy == "tail_yield_engine":
        raw_price = metadata.get("yes_price")
        if raw_price is None:
            return {"ok": False, "clord_id": clord_id, "error": "missing yes_price in metadata"}
        price = Decimal(str(raw_price)).quantize(Decimal("0.001"), rounding=ROUND_DOWN)

    else:
        return {"ok": False, "clord_id": clord_id, "error": f"no order logic for strategy '{strategy}'"}

    size_usdc  = _size_from_signal(signal, "BUY")
    if size_usdc <= 0:
        return {"ok": False, "clord_id": clord_id, "error": "computed size is zero"}

    # Compute share quantity once in Decimal to avoid float drift across all three uses below.
    size_shares = (size_usdc / price).quantize(Decimal("0.0001"), rounding=ROUND_DOWN)

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
    # Note: tick_size is market-specific (0.01 or 0.001). Defaulting to 0.001.
    # If the CLOB rejects with a tick size error, fetch via client.get_tick_size(token_id).
    try:
        from py_clob_client.clob_types import OrderArgs, OrderType, PartialCreateOrderOptions
        order_args = OrderArgs(
            token_id=token_id,
            price=float(price),
            size=float(size_shares),
            side="BUY",
        )
        _opts = PartialCreateOrderOptions(neg_risk=True) if signal.get("neg_risk") else None

        def _submit():
            order_args.expiration = str(expiration)
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
        # Record working qty in positions table
        db.upsert_position(
            market_id, token_id, "YES",
            delta_working_buy=float(size_shares),
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
        logger.error(f"Order submission failed after retries | clord_id={clord_id}: {e}")
        db.update_order_status(clord_id, "REJECTED", error_msg=str(e))
        alerts.order_rejected(
            strategy=strategy,
            market_id=market_id,
            question=metadata.get("question", ""),
            clord_id=clord_id,
            error=str(e),
        )
        nats_bus.publish(
            f"pm.execution.rejected.{strategy}.{market_id}",
            {
                "clord_id":  clord_id,
                "strategy":  strategy,
                "market_id": market_id,
                "error":     str(e),
                "ts":        datetime.now(timezone.utc).isoformat(),
            },
        )
        return {"ok": False, "clord_id": clord_id, "error": str(e)}


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
        def _cancel():
            return client.cancel(order_id=exchange_order_id)
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

    price_d      = Decimal(str(price)).quantize(Decimal("0.001"), rounding=ROUND_DOWN)
    size_shares  = net_shares.quantize(Decimal("0.0001"), rounding=ROUND_DOWN)
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
    db.upsert_position(market_id, token_id, "YES", delta_working_sell=float(size_shares))

    try:
        from py_clob_client.clob_types import OrderArgs, OrderType, PartialCreateOrderOptions
        order_args = OrderArgs(
            token_id=token_id,
            price=float(price_d),
            size=float(size_shares),
            side="SELL",
        )
        _opts = PartialCreateOrderOptions(neg_risk=True) if position.get("neg_risk") else None

        def _submit():
            order_args.expiration = str(expiration)
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
        logger.error(f"Exit order submission failed | clord_id={clord_id}: {e}")
        db.update_order_status(clord_id, "REJECTED", error_msg=str(e))
        # Undo working_sell delta so position state stays accurate
        db.upsert_position(market_id, token_id, "YES", delta_working_sell=-float(size_shares))
        return {"ok": False, "clord_id": clord_id, "error": str(e)}


def cancel_all_open_orders(client) -> dict:
    """
    Cancel every non-terminal order.
    Uses client.cancel_all() for CLOB orders (one API call), then closes any
    DB-only orders (no exchange_order_id) that never reached the CLOB.
    Returns a summary dict: {attempted, succeeded, failed, db_only}.
    """
    open_orders = db.get_open_orders()
    attempted   = len(open_orders)
    db_only     = sum(1 for o in open_orders if not o.get("exchange_order_id"))
    clob_count  = attempted - db_only

    # Single API call cancels everything on the CLOB
    clob_failed = 0
    if clob_count > 0:
        try:
            def _cancel_all():
                return client.cancel_all()
            _backoff_retry(_cancel_all)
        except Exception as e:
            logger.error(f"cancel_all CLOB call failed: {e}")
            clob_failed = clob_count

    # Sync DB for all open orders regardless of API outcome
    now = datetime.now(timezone.utc)
    for order in open_orders:
        db.update_order_status(order["clord_id"], "CANCELED", canceled_at=now)

    succeeded = attempted - clob_failed
    summary = {
        "attempted": attempted,
        "succeeded": succeeded,
        "failed":    clob_failed,
        "db_only":   db_only,
    }
    logger.warning(
        f"cancel_all_open_orders | attempted={attempted} "
        f"succeeded={succeeded} failed={clob_failed} db_only={db_only}"
    )
    alerts.cancel_all_fired(summary)
    return summary
