# execution/executor.py — daemon thread that polls signals and places orders
#
# Designed to be started once from scheduler.py (mirrors the Flask server thread).
# The loop is intentionally simple: poll → gate → place → sleep. Each signal
# is processed independently — one failure never blocks the others.
#
# Also polls open orders for fill updates on every cycle so the positions
# table stays current without a separate poller.
#
# Fast-path wake: subscribes to pm.signals.> via NATS. When a strategy engine
# emits a new signal, the executor wakes immediately rather than waiting the
# full EXECUTOR_POLL_SECS interval. Falls back to timed poll when NATS is absent.

import logging
import threading
from datetime import datetime, timezone

import alerts
import db
import nats_bus
from config import (
    EXECUTION_MIN_SCORE,
    EXECUTION_STRATEGIES,
    EXECUTOR_POLL_SECS,
    MAX_SIGNAL_AGE_SECS,
)
from execution import pre_trade_gate, order_manager, exit_manager, connection_checker, reconciler
from execution.auth import get_client

logger = logging.getLogger(__name__)

# ── Pause flag ────────────────────────────────────────────────────────────────
# Set   = executor is paused  (no new signals processed; fill polling continues)
# Clear = executor is running (default)
_PAUSE_FLAG = threading.Event()

# ── Fast-path wake event ──────────────────────────────────────────────────────
# Set by _on_signal_message() when pm.signals.> fires — wakes the executor
# immediately instead of waiting the full EXECUTOR_POLL_SECS sleep.
_SIGNAL_EVENT = threading.Event()


def _on_signal_message(subject: str, data: dict) -> None:
    """NATS subscription callback: wake the executor when a new signal arrives."""
    _SIGNAL_EVENT.set()


def pause() -> None:
    _PAUSE_FLAG.set()
    logger.warning("Executor PAUSED — new signal processing halted")
    alerts.executor_paused()


def resume() -> None:
    _PAUSE_FLAG.clear()
    logger.info("Executor RESUMED — signal processing active")
    alerts.executor_resumed()


def is_paused() -> bool:
    return _PAUSE_FLAG.is_set()


def _process_signals(client) -> int:
    """Fetch and attempt execution of all eligible signals. Returns count placed."""
    signals = db.get_executable_signals(
        min_score=EXECUTION_MIN_SCORE,
        strategies=EXECUTION_STRATEGIES,
        max_age_secs=MAX_SIGNAL_AGE_SECS,
    )

    if not signals:
        return 0

    placed = 0
    for signal in signals:
        signal_id = signal.get("id")
        strategy  = signal.get("strategy")

        approved, reason = pre_trade_gate.check(signal)
        if not approved:
            logger.info(f"Signal {signal_id} ({strategy}) rejected: {reason}")
            # Mark stale signals as executed so they don't clog the queue
            if "stale" in reason:
                db.mark_signal_executed(signal_id)
            continue

        result = order_manager.place_order(signal, client)
        if result["ok"]:
            placed += 1
        else:
            logger.warning(
                f"Order failed | signal_id={signal_id} | "
                f"clord_id={result['clord_id']} | error={result['error']}"
            )

    return placed


def _evaluate_reprice(order: dict, snapshot: dict) -> dict | None:
    """
    Inline re-evaluation for a GTD-expired unfilled order.
    Re-runs strategy logic against the current snapshot.
    Returns a minimal signal dict if edge still exists, None to walk away.
    """
    strategy  = order["strategy"]
    yes_price = snapshot.get("yes_price")
    if yes_price is None:
        return None

    if strategy == "spread_engine":
        from agents.spread_engine import _analyse_snapshot
        result = _analyse_snapshot(snapshot)
        if result is None:
            return None
        result["id"]        = order.get("signal_id")
        result["token_ids"] = order.get("token_ids") or [order["token_id"]]
        return result

    if strategy == "tail_yield_engine":
        from config import YIELD_MIN_PRICE, YIELD_HOURS_TO_EXPIRY
        yp  = float(yes_price)
        htc = snapshot.get("hours_to_close")
        if htc is None or yp < YIELD_MIN_PRICE or float(htc) > YIELD_HOURS_TO_EXPIRY:
            return None
        yield_pct = ((1.0 / yp) - 1) * 100
        return {
            "id":           order.get("signal_id"),
            "strategy":     strategy,
            "market_id":    order["market_id"],
            "signal_score": round(min(yield_pct / 5.0, 1.0), 4),
            "token_ids":    order.get("token_ids") or [order["token_id"]],
            "metadata":     {"yes_price": yp, "kelly_fraction": None, "question": snapshot.get("question", "")},
        }

    return None


def _reprice_expired_orders(client) -> int:
    """
    Detect GTD-expired unfilled orders, re-evaluate edge, and repost if still valid.
    Returns count of reprices placed.
    """
    expired = db.get_expired_unfilled_orders()
    if not expired:
        return 0

    repriced = 0
    for order in expired:
        order_id  = order["id"]
        strategy  = order["strategy"]
        market_id = order["market_id"]

        # Claim immediately — prevents duplicate reprice on next cycle
        db.mark_order_repriced(order_id)

        snapshot = db.get_snapshot_for_reprice(market_id)
        if not snapshot:
            logger.info(f"Reprice skipped — no snapshot | market={market_id}")
            continue

        signal = _evaluate_reprice(order, snapshot)
        if not signal:
            logger.info(f"Reprice skipped — edge gone | strategy={strategy} market={market_id}")
            continue

        approved, reason = pre_trade_gate.check_reprice(signal)
        if not approved:
            logger.info(f"Reprice gate rejected | {reason}")
            continue

        result = order_manager.place_order(signal, client, reprice_of=order_id)
        if result["ok"]:
            repriced += 1
            logger.info(
                f"Repriced | original_id={order_id} | "
                f"new_clord_id={result['clord_id']} | strategy={strategy}"
            )
            nats_bus.publish(
                f"pm.execution.repriced.{strategy}.{market_id}",
                {
                    "original_order_id": order_id,
                    "new_clord_id":      result["clord_id"],
                    "strategy":          strategy,
                    "market_id":         market_id,
                    "ts":                datetime.now(timezone.utc).isoformat(),
                },
            )
        else:
            logger.warning(
                f"Reprice order failed | original_id={order_id} | error={result['error']}"
            )

    return repriced


def _poll_open_orders(client) -> None:
    """Check fill status on all non-terminal orders."""
    open_orders = db.get_open_orders()
    for order in open_orders:
        try:
            order_manager.poll_order_status(order, client)
        except Exception as e:
            logger.error(
                f"poll_order_status error for clord_id={order.get('clord_id')}: {e}"
            )


def run_executor() -> None:
    """
    Main executor loop. Runs forever — designed to be a daemon thread.
    Initialises the CLOB client on first iteration so a startup failure
    doesn't crash the entire process; it logs and retries next cycle.
    """
    logger.info(
        f"Executor starting | poll={EXECUTOR_POLL_SECS}s | "
        f"strategies={EXECUTION_STRATEGIES} | min_score={EXECUTION_MIN_SCORE}"
    )

    client  = None
    _cycle  = 0
    _CHECK_EVERY      = 30   # run connection check on cycle 1 then every ~5 min (30 × 10s)
    _RECONCILE_EVERY  = 30   # same cadence — DB↔CLOB order state sync

    while True:
        placed   = 0
        repriced = 0
        _cycle  += 1
        try:
            if client is None:
                client = get_client()
                logger.info("Executor: CLOB client ready")

            if _cycle == 1 or _cycle % _CHECK_EVERY == 0:
                try:
                    connection_checker.run_check(client)
                except Exception as _ce:
                    logger.warning(f"Connection check error: {_ce}")

            # Periodic order-state reconciliation. Catches DB rows stuck in
            # OPEN/SENT/PENDING_SUBMISSION whose CLOB-side state has drifted
            # (filled, canceled, or expired) and the per-cycle poll missed.
            # Also detects orphan CLOB orders (alert only).
            if _cycle == 1 or _cycle % _RECONCILE_EVERY == 0:
                try:
                    reconciler.reconcile_orders(client)
                except Exception as _re:
                    logger.warning(f"Reconciler error (orders): {_re}")
                try:
                    reconciler.reconcile_positions()
                except Exception as _re:
                    logger.warning(f"Reconciler error (positions): {_re}")

            # Sync pause flag from DB so UI toggle takes effect within one cycle
            try:
                paused_in_db = db.get_config("executor_paused", "false") == "true"
                if paused_in_db and not _PAUSE_FLAG.is_set():
                    _PAUSE_FLAG.set()
                    logger.warning("Executor PAUSED via DB config")
                elif not paused_in_db and _PAUSE_FLAG.is_set():
                    _PAUSE_FLAG.clear()
                    logger.info("Executor RESUMED via DB config")
            except Exception as _e:
                logger.debug(f"Could not sync pause flag from DB: {_e}")

            if _PAUSE_FLAG.is_set():
                logger.debug("Executor paused — skipping signal processing")
            else:
                placed = _process_signals(client)

            _poll_open_orders(client)       # always runs — fills must track regardless of pause
            repriced = _reprice_expired_orders(client)  # GTD-expired → re-evaluate → repost

            if placed or repriced:
                logger.info(f"Executor cycle: {placed} placed, {repriced} repriced")

        except Exception as e:
            logger.error(f"Executor cycle error: {e}", exc_info=True)
            # Reset client on auth/connection errors so next cycle re-initialises
            if "credential" in str(e).lower() or "connect" in str(e).lower():
                client = None

        nats_bus.publish("pm.heartbeat.executor", {
            "ts":       datetime.now(timezone.utc).isoformat(),
            "paused":   _PAUSE_FLAG.is_set(),
            "placed":   placed,
            "repriced": repriced,
        })

        # Fast-path: wake immediately on new NATS signal, fall back to timed poll
        _SIGNAL_EVENT.wait(timeout=EXECUTOR_POLL_SECS)
        _SIGNAL_EVENT.clear()


def start_executor() -> threading.Thread:
    """
    Start the executor as a daemon thread. Returns the thread (already started).
    Call once from scheduler.py before the pipeline loop.
    """
    # Exit manager: seed position index + register NATS subscriptions for TP exits.
    # Must run before the NATS worker starts so subscriptions are registered in time.
    exit_manager.start()

    # Fast-path wake: register NATS subscription before starting the thread
    # so the worker picks it up when it connects. Falls back to timed poll
    # if NATS_URL is unset.
    nats_bus.subscribe("pm.signals.>", _on_signal_message)

    t = threading.Thread(target=run_executor, daemon=True, name="executor")
    t.start()
    logger.info("Executor thread started")
    return t
