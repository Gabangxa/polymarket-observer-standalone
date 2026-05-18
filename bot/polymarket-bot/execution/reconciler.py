# execution/reconciler.py — periodic DB ↔ CLOB state reconciliation
#
# The executor's per-cycle poll_open_orders covers the happy path (poll each
# DB-open order via client.get_order). This module covers the cases where
# that loop misses state:
#
#   • DB says OPEN but the CLOB has no record of the order (already filled,
#     canceled, or expired). poll_order_status can't always recover this if
#     get_order() returns an error for a long-since-terminal order.
#   • DB has an order stuck in PENDING_SUBMISSION or SENT for many minutes —
#     the process crashed between db.insert_order and the status update.
#   • The CLOB has a working order that isn't in our DB at all (manual order,
#     leftover from a previous deployment, signature replay). Operator needs
#     to know about these.
#
# Reconciliation is best-effort and idempotent. It never deletes data; it
# only marks terminal states or logs orphans.

import logging
import os
from datetime import datetime, timezone, timedelta
from decimal import Decimal

import alerts
import db
import api as polymarket_api
from execution import order_manager

logger = logging.getLogger(__name__)

# Orders stuck in SENT or PENDING_SUBMISSION past this threshold are considered
# zombies — the original submission flow didn't complete, retry won't help.
# Configurable so peak CLOB latency (e.g. major sports event resolution) doesn't
# falsely classify slow-but-valid submissions as dead. Set RECONCILER_ZOMBIE_CUTOFF_MINS
# to override the 30min default.
_ZOMBIE_AGE_MINS = int(os.environ.get("RECONCILER_ZOMBIE_CUTOFF_MINS", "30"))

# Per-token drift tolerance (shares). Fills round share quantities to 4dp;
# anything below this is dust from successive partial fills and rounding.
_POSITION_DRIFT_TOLERANCE = Decimal("0.01")


def _classify(db_orders: list[dict], clob_ids: set[str]) -> dict:
    """Bucket DB orders by reconciliation action. Read-only — no DB writes here."""
    buckets = {
        "in_sync":     [],   # status=OPEN, exchange_order_id in clob_ids — nothing to do
        "missing_eoi": [],   # exchange_order_id is NULL → never registered on CLOB
        "terminal":    [],   # DB says OPEN but CLOB doesn't have it → must be filled/canceled
        "zombie":      [],   # PENDING_SUBMISSION / SENT stuck for too long
    }
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=_ZOMBIE_AGE_MINS)

    for o in db_orders:
        status = o.get("status")
        eoi    = o.get("exchange_order_id")
        created_at = o.get("created_at")

        if not eoi:
            if created_at and created_at < cutoff:
                buckets["zombie"].append(o)
            else:
                buckets["missing_eoi"].append(o)
            continue

        if status in ("PENDING_SUBMISSION", "SENT"):
            if created_at and created_at < cutoff:
                buckets["zombie"].append(o)
            else:
                buckets["missing_eoi"].append(o)
            continue

        # status in ('OPEN', 'PARTIALLY_FILLED')
        if eoi in clob_ids:
            buckets["in_sync"].append(o)
        else:
            buckets["terminal"].append(o)

    return buckets


def reconcile_orders(client) -> dict:
    """
    Diff DB non-terminal orders against the CLOB's live open-order set and
    reconcile drift. Returns a summary dict for logging / alerting.

    The CLOB call is paginated client.get_orders() — one API trip total.
    Per-order recovery only happens for the (small) set of drifted orders.
    """
    db_orders = db.get_open_orders()
    if not db_orders:
        return {"db_open": 0, "clob_open": 0, "reconciled": 0, "zombies": 0, "orphans": 0}

    try:
        clob_orders = client.get_orders() or []
    except Exception as e:
        logger.warning(f"reconcile_orders: CLOB get_orders failed: {e}")
        return {"db_open": len(db_orders), "clob_open": 0, "reconciled": 0,
                "zombies": 0, "orphans": 0, "error": str(e)}

    # Each CLOB order dict has an 'id' field — that's our exchange_order_id.
    clob_ids = {o.get("id") for o in clob_orders if o.get("id")}
    buckets  = _classify(db_orders, clob_ids)

    reconciled = 0
    for o in buckets["terminal"]:
        # Try the single-order fetch one more time — if get_order returns a
        # real terminal status (FILLED/CANCELED) with a fill price, poll_order_status
        # will write the correct outcome. If get_order also fails, we mark CANCELED
        # as the safe default (working qty is definitely zero either way).
        try:
            order_manager.poll_order_status(o, client)
            reconciled += 1
        except Exception as e:
            logger.warning(
                f"reconcile_orders: get_order recovery failed for "
                f"clord_id={o.get('clord_id')}: {e} — marking CANCELED"
            )
            db.update_order_status(
                o["clord_id"], "CANCELED",
                canceled_at=datetime.now(timezone.utc),
                error_msg=f"reconciler: missing from CLOB, get_order failed: {e}",
            )
            reconciled += 1

    # Zombies: PENDING_SUBMISSION / SENT or missing-EOI orders stuck past threshold.
    # Before declaring them dead, try one last CLOB lookup for any zombie that
    # already has an exchange_order_id — a slow ACK that arrived after the
    # bulk get_orders() but before this loop would otherwise be wrongly REJECTED.
    # On confirmed-missing or no EOI: mark REJECTED so the signal queue clears.
    zombies      = 0
    zombie_saved = 0
    for o in buckets["zombie"]:
        eoi = o.get("exchange_order_id")
        if eoi:
            try:
                order_manager.poll_order_status(o, client)
                zombie_saved += 1
                continue
            except Exception as e:
                logger.info(
                    f"reconcile_orders: zombie re-check failed for "
                    f"clord_id={o.get('clord_id')} eoi={eoi}: {e} — marking REJECTED"
                )
        db.update_order_status(
            o["clord_id"], "REJECTED",
            error_msg=f"reconciler: zombie ({o.get('status')}, no progress in {_ZOMBIE_AGE_MINS}min)",
        )
        zombies += 1

    # Orphans: CLOB has an order we never recorded. Could be a manual order, a
    # cross-deployment leftover, or a serious replay bug. Don't auto-cancel —
    # operator must look at it.
    db_eoi_set = {o.get("exchange_order_id") for o in db_orders if o.get("exchange_order_id")}
    orphans = []
    for c in clob_orders:
        cid = c.get("id")
        if cid and cid not in db_eoi_set:
            orphans.append(cid)
            logger.warning(
                f"reconcile_orders: ORPHAN CLOB order id={cid} "
                f"market={c.get('market')} side={c.get('side')} "
                f"price={c.get('price')} size_remaining={c.get('size_remaining')}"
            )

    if orphans:
        alerts.reconciler_orphans(orphans)

    summary = {
        "db_open":      len(db_orders),
        "clob_open":    len(clob_orders),
        "in_sync":      len(buckets["in_sync"]),
        "reconciled":   reconciled,
        "zombies":      zombies,
        "zombie_saved": zombie_saved,
        "orphans":      len(orphans),
    }
    logger.info(
        f"reconcile_orders: db={summary['db_open']} clob={summary['clob_open']} "
        f"in_sync={summary['in_sync']} reconciled={summary['reconciled']} "
        f"zombies={summary['zombies']} zombie_saved={summary['zombie_saved']} "
        f"orphans={summary['orphans']}"
    )
    return summary


def _wallet_address() -> str:
    """
    Return the address whose Polymarket positions we should reconcile against.
    For sig_type 1/3 (proxy / 1271): POLYMARKET_FUNDER. For sig_type 0 (EOA),
    a real lookup needs the signer's derived address — currently not wired.
    Returns "" if no address is available; callers should skip reconciliation.
    """
    return os.environ.get("POLYMARKET_FUNDER", "").strip()


def reconcile_positions() -> dict:
    """
    Diff the bot's positions table against the Polymarket Data API's view of
    the wallet's actual holdings. Detection only — never auto-writes to the
    positions table. Drift means either:
      • A fill the bot's poll_order_status missed (correct value lives on the
        exchange; operator should resync).
      • A manual exchange action (deposit, transfer, manual order/cancel).
      • A bug in the position-update path.

    Tolerates dust below _POSITION_DRIFT_TOLERANCE shares.
    Returns a summary dict with drift counts; alerts only when drift > 0.
    """
    address = _wallet_address()
    if not address:
        return {
            "db_rows":       0,
            "wallet_rows":   0,
            "drift_markets": 0,
            "skipped":       "no POLYMARKET_FUNDER set",
        }

    db_positions = db.get_all_open_positions()
    wallet       = polymarket_api.get_user_positions(address)

    # Aggregate DB net shares per token_id.
    # YES = total_bought - total_sold; the bot only longs YES today, so net = exposure.
    db_by_token: dict[str, Decimal] = {}
    for p in db_positions:
        tok = p.get("token_id")
        if not tok:
            continue
        net = Decimal(str(p.get("total_bought") or 0)) - Decimal(str(p.get("total_sold") or 0))
        if net != 0:
            db_by_token[tok] = db_by_token.get(tok, Decimal("0")) + net

    # Wallet positions: each entry has 'asset' (= token_id) and 'size' (shares).
    wallet_by_token: dict[str, Decimal] = {}
    for w in wallet:
        tok = w.get("asset") or w.get("token_id")
        if not tok:
            continue
        try:
            size = Decimal(str(w.get("size") or 0))
        except Exception:
            continue
        if size != 0:
            wallet_by_token[tok] = wallet_by_token.get(tok, Decimal("0")) + size

    drift_markets = []
    all_tokens = set(db_by_token) | set(wallet_by_token)
    for tok in all_tokens:
        db_qty     = db_by_token.get(tok, Decimal("0"))
        wallet_qty = wallet_by_token.get(tok, Decimal("0"))
        if abs(db_qty - wallet_qty) > _POSITION_DRIFT_TOLERANCE:
            drift_markets.append({
                "token_id":   tok,
                "db_qty":     float(db_qty),
                "wallet_qty": float(wallet_qty),
                "delta":      float(wallet_qty - db_qty),
            })
            logger.warning(
                f"reconcile_positions: DRIFT | token={tok[:20]}… "
                f"db_net={db_qty} wallet={wallet_qty} delta={wallet_qty - db_qty}"
            )

    summary = {
        "db_rows":       len(db_by_token),
        "wallet_rows":   len(wallet_by_token),
        "drift_markets": len(drift_markets),
        "drift_detail":  drift_markets[:10],   # cap for log payload
    }

    if drift_markets:
        alerts.position_drift(summary)

    logger.info(
        f"reconcile_positions: db_tokens={summary['db_rows']} "
        f"wallet_tokens={summary['wallet_rows']} "
        f"drift={summary['drift_markets']}"
    )
    return summary
