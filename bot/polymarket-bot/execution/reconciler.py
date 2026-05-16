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
from datetime import datetime, timezone, timedelta

import alerts
import db
from execution import order_manager

logger = logging.getLogger(__name__)

# Orders stuck in SENT or PENDING_SUBMISSION past this threshold are considered
# zombies — the original submission flow didn't complete, retry won't help.
_ZOMBIE_AGE_MINS = 15


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
    # The original submission never completed — close them out so the signal queue clears.
    for o in buckets["zombie"]:
        db.update_order_status(
            o["clord_id"], "REJECTED",
            error_msg=f"reconciler: zombie ({o.get('status')}, no progress in {_ZOMBIE_AGE_MINS}min)",
        )
    zombies = len(buckets["zombie"])

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
        "db_open":     len(db_orders),
        "clob_open":   len(clob_orders),
        "in_sync":     len(buckets["in_sync"]),
        "reconciled":  reconciled,
        "zombies":     zombies,
        "orphans":     len(orphans),
    }
    logger.info(
        f"reconcile_orders: db={summary['db_open']} clob={summary['clob_open']} "
        f"in_sync={summary['in_sync']} reconciled={summary['reconciled']} "
        f"zombies={summary['zombies']} orphans={summary['orphans']}"
    )
    return summary
