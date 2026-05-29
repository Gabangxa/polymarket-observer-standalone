# scheduler.py — continuous pipeline runner + HTTP keep-alive for Replit
#
# Starts a Flask server in a background thread (keeps Replit "always on" alive),
# then runs the pipeline on a loop.
#
# Run pattern:
#   Run 1, 13, 25, ...  → full run including market scanner
#   All other runs      → collect + analyse only (faster, cheaper on API)

import logging
import os
import signal
import sys
import threading
import time
from datetime import datetime, timezone

# ── Logging (must happen before any other imports that use loggers) ────────────
os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(f"logs/run_{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.log"),
        logging.StreamHandler(sys.stdout),
    ],
)

logger = logging.getLogger("scheduler")

from config import POLL_INTERVAL_SECONDS, EXECUTOR_HEARTBEAT_STALE_SECS
import alerts

# Refresh watchlist every N runs  (default: every 12 runs ≈ 6min at 30s interval)
SCAN_INTERVAL_RUNS = 12

# Bound the graceful-shutdown cancel attempt. Railway sends SIGTERM and waits
# ~10s before SIGKILL — leave 2s of headroom so the process exits cleanly.
SHUTDOWN_CANCEL_TIMEOUT_SECS = 8.0

_shutdown_requested = threading.Event()


def _cancel_all_bounded(context: str) -> None:
    """
    Cancel every open CLOB order with a bounded timeout so the caller never
    blocks longer than SHUTDOWN_CANCEL_TIMEOUT_SECS. Logs the outcome; never
    raises. No-op when no wallet key is configured (nothing to cancel).

    Shared by the SIGTERM/SIGINT graceful shutdown and the hung-executor
    watchdog restart — both must drop GTD orders before the process exits so
    they don't dangle live on the CLOB through the restart.
    """
    if not os.environ.get("POLYGON_PRIVATE_KEY"):
        logger.warning(f"{context}: no POLYGON_PRIVATE_KEY — nothing to cancel")
        return

    result = {"summary": None, "error": None}
    done = threading.Event()

    def _do_cancel():
        try:
            from execution.auth import get_client
            from execution.order_manager import cancel_all_open_orders
            result["summary"] = cancel_all_open_orders(get_client())
        except Exception as e:
            result["error"] = repr(e)
        finally:
            done.set()

    threading.Thread(target=_do_cancel, daemon=True, name="cancel-all-bounded").start()

    if done.wait(timeout=SHUTDOWN_CANCEL_TIMEOUT_SECS):
        if result["error"]:
            logger.error(f"{context}: cancel-all failed: {result['error']}")
        else:
            logger.warning(f"{context}: cancel-all summary: {result['summary']}")
    else:
        logger.error(
            f"{context}: cancel-all timed out after {SHUTDOWN_CANCEL_TIMEOUT_SECS}s — "
            "orders may remain open; reconciler will catch on restart"
        )


def _graceful_shutdown(signum, frame):
    """
    Cancel every open CLOB order before the process exits. Without this,
    GTD orders remain live on the CLOB through a Railway deploy/restart and
    new signals on the next boot can stack additional exposure against the
    same bankroll — temporary over-exposure beyond MAX_PORTFOLIO_PCT until
    the reconciler catches drift ~5 min later.
    """
    if _shutdown_requested.is_set():
        logger.warning(f"Second shutdown signal ({signum}) received — exiting immediately")
        sys.exit(1)
    _shutdown_requested.set()
    logger.warning(f"Shutdown signal ({signum}) received — cancelling open orders")
    _cancel_all_bounded("shutdown")
    sys.exit(0)


def _restart_for_hung_executor(age_secs: float) -> None:
    """
    The executor heartbeat is stale — the thread is alive but not progressing
    (the 2026-05-18 silent-hang failure mode). Cancel open orders, then exit
    NON-ZERO so Railway (restartPolicyType=ON_FAILURE) brings up a clean
    process. We exit rather than spawn a second executor thread: the hung
    thread is still alive, and a second executor would double-execute signals.
    """
    logger.critical(
        f"Executor heartbeat stale ({age_secs:.0f}s > {EXECUTOR_HEARTBEAT_STALE_SECS}s) "
        "— executor hung; cancelling orders and exiting for a clean restart"
    )
    try:
        alerts.pipeline_crashed(-1, Exception(f"executor hung (heartbeat stale {age_secs:.0f}s)"))
    except Exception as _ae:
        logger.error(f"hung-executor alert failed: {_ae}")
    _cancel_all_bounded("hung-executor restart")
    # Non-zero exit → ON_FAILURE restart policy starts a fresh process.
    sys.exit(1)


def main():
    # Register shutdown handlers before any thread or client init so a SIGTERM
    # during startup still triggers cancel-all.
    signal.signal(signal.SIGTERM, _graceful_shutdown)
    signal.signal(signal.SIGINT, _graceful_shutdown)

    # Import here so logging is configured first
    from server import start_server
    from main import run_pipeline

    from config import EXECUTION_STRATEGIES, EXECUTION_MIN_SCORE
    import db as _db
    bankroll = _db.get_bankroll()
    logger.info("=" * 60)
    logger.info("polymarket-bot starting up")
    logger.info(f"Poll interval    : {POLL_INTERVAL_SECONDS}s")
    logger.info(f"Scan interval    : every {SCAN_INTERVAL_RUNS} runs")
    logger.info(f"Active strategies: {EXECUTION_STRATEGIES or '(none — execution disabled)'}")
    logger.info(f"Min score        : {EXECUTION_MIN_SCORE}")
    logger.info(f"Bankroll USDC    : {bankroll if bankroll > 0 else '(not set — orders blocked)'}")
    logger.info("=" * 60)

    # Fail loudly if the alert channel is unconfigured — a silent alerting path
    # hides every other failure, including crashes (see 2026-05 incident where
    # the executor hang produced no Discord alert because the webhook was unset).
    alerts.check_alerting_configured()

    # On Railway each service gets its own PORT — bind to it so health checks
    # and the public URL work. BOT_PORT is a local/Replit fallback only.
    port = int(os.environ.get("PORT", os.environ.get("BOT_PORT", 8080)))
    start_server(host="0.0.0.0", port=port)

    # Start execution layer — polls signals table and places CLOB orders.
    # Gate: POLYGON_PRIVATE_KEY must be set in Railway service variables.
    # Bankroll is managed via the dashboard UI (stored in bot_config.bankroll_usdc).
    executor_thread = None
    if os.environ.get("POLYGON_PRIVATE_KEY"):
        from execution.executor import start_executor
        executor_thread = start_executor()
        if bankroll <= 0:
            logger.warning(
                "Executor started but bankroll is 0 — "
                "set it in the dashboard Execution Console before placing orders."
            )
    else:
        logger.warning(
            "Executor NOT started — set POLYGON_PRIVATE_KEY in Railway service variables "
            "to enable live execution."
        )

    run_count = 0

    while True:
        run_count += 1
        skip_scan = (run_count % SCAN_INTERVAL_RUNS != 1)

        # Watchdog: detect a DEAD or HUNG executor.
        #   dead  → is_alive() False. Safe to respawn in-process (no concurrency).
        #   hung  → alive but heartbeat stale (the 2026-05-18 silent-hang mode).
        #           Restart the whole process instead of spawning a second,
        #           double-executing thread.
        if executor_thread is not None:
            if not executor_thread.is_alive():
                logger.error("Executor thread has died — restarting", exc_info=False)
                alerts.pipeline_crashed(run_count, Exception("executor thread died unexpectedly"))
                from execution.executor import start_executor
                executor_thread = start_executor()
            else:
                age = _db.seconds_since_executor_heartbeat()
                if age is not None and age > EXECUTOR_HEARTBEAT_STALE_SECS:
                    _restart_for_hung_executor(age)   # cancels orders, exits non-zero

        logger.info(f"\n{'='*40}")
        logger.info(
            f"Run #{run_count} at {datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}"
            + (" [+scan]" if not skip_scan else "")
        )

        try:
            run_pipeline(skip_scan=skip_scan)
        except Exception as e:
            logger.error(f"Pipeline run #{run_count} failed: {e}", exc_info=True)
            alerts.pipeline_crashed(run_count, e)

        logger.info(f"Sleeping {POLL_INTERVAL_SECONDS}s...")
        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
