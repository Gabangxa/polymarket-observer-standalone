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
import sys
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

from config import POLL_INTERVAL_SECONDS
import alerts

# Refresh watchlist every N runs  (default: every 12 runs ≈ 6min at 30s interval)
SCAN_INTERVAL_RUNS = 12


def main():
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

        # Watchdog: restart executor thread if it has died silently
        if executor_thread is not None and not executor_thread.is_alive():
            logger.error("Executor thread has died — restarting", exc_info=False)
            alerts.pipeline_crashed(run_count, Exception("executor thread died unexpectedly"))
            from execution.executor import start_executor
            executor_thread = start_executor()

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
