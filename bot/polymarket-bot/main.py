# main.py — run the full pipeline once
# Order: schema init → scanner → collector → [spread, neg_risk, reversion] → outcome_tracker

import glob
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone

from config import LOGS_DIR, STATE_DIR, LOG_RETENTION_DAYS, ZERO_SIGNAL_STREAK_WARN

os.makedirs(LOGS_DIR, exist_ok=True)
os.makedirs(STATE_DIR, exist_ok=True)

_STREAKS_FILE = os.path.join(STATE_DIR, "engine_streaks.json")

today    = datetime.now(timezone.utc).strftime("%Y-%m-%d")
log_path = os.path.join(LOGS_DIR, f"run_{today}.log")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(log_path),
        logging.StreamHandler(sys.stdout),
    ],
)

logger = logging.getLogger("main")

import alerts
import db
from agents import market_scanner, data_collector
from agents import spread_engine, neg_risk_engine, reversion_engine, outcome_tracker
from agents import odds_shift_engine, hindsight_logger


# ── Helpers ───────────────────────────────────────────────────────────────────

def _cleanup_old_logs() -> None:
    """Delete .log files in LOGS_DIR older than LOG_RETENTION_DAYS days."""
    cutoff = time.time() - LOG_RETENTION_DAYS * 86_400
    removed = 0
    for path in glob.glob(os.path.join(LOGS_DIR, "*.log")):
        try:
            if os.path.getmtime(path) < cutoff:
                os.remove(path)
                removed += 1
        except OSError:
            pass
    if removed:
        logger.info(f"Log cleanup: removed {removed} file(s) older than {LOG_RETENTION_DAYS} days")


def _update_signal_streaks(results: dict) -> None:
    """
    Track consecutive zero-signal runs per engine.
    Writes state to state/engine_streaks.json and logs a WARNING when any
    engine exceeds ZERO_SIGNAL_STREAK_WARN consecutive zero-signal runs.
    Crashed runs (error key present) are skipped so a crash doesn't reset the streak.
    """
    try:
        with open(_STREAKS_FILE) as f:
            streaks = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        streaks = {}

    now = datetime.now(timezone.utc).isoformat()
    agents = results.get("agents", {})

    for engine in ("spread_engine", "neg_risk_engine", "reversion_engine"):
        result = agents.get(engine, {})
        if "error" in result:
            continue  # crashed run — don't touch the streak counter

        signals = result.get("signals", 0)
        entry   = streaks.get(engine, {"streak": 0, "last_signal_at": None})

        if signals > 0:
            streaks[engine] = {"streak": 0, "last_signal_at": now}
        else:
            streak = entry["streak"] + 1
            streaks[engine] = {"streak": streak, "last_signal_at": entry["last_signal_at"]}
            if streak >= ZERO_SIGNAL_STREAK_WARN:
                last = entry["last_signal_at"] or "never"
                logger.warning(
                    f"ZERO-SIGNAL STREAK: {engine} has produced 0 signals for "
                    f"{streak} consecutive runs (~{streak * 5} min). "
                    f"Last signal at: {last}"
                )
                alerts.zero_signal_streak(engine, streak, entry["last_signal_at"])

    try:
        with open(_STREAKS_FILE, "w") as f:
            json.dump(streaks, f, indent=2)
    except OSError as e:
        logger.warning(f"Could not write streaks file: {e}")


def run_pipeline(skip_scan=False):
    run_start = datetime.now(timezone.utc).isoformat()
    results   = {"started_at": run_start, "agents": {}}

    logger.info("=" * 60)
    logger.info(f"Pipeline run started: {run_start}")
    logger.info("=" * 60)

    _cleanup_old_logs()

    # Ensure schema exists (idempotent)
    try:
        db.init_schema()
    except Exception as e:
        logger.error(f"Schema init failed: {e}")
        # Don't abort — DB might already be set up

    if not skip_scan:
        try:
            result = market_scanner.run()
            results["agents"]["market_scanner"] = result
            logger.info(f"Scanner: {result}")
        except Exception as e:
            logger.error(f"market_scanner crashed: {e}", exc_info=True)
            results["agents"]["market_scanner"] = {"error": str(e)}
    else:
        logger.info("Skipping scanner (skip_scan=True)")

    try:
        result = data_collector.run()
        results["agents"]["data_collector"] = result
        logger.info(f"Collector: {result}")
    except Exception as e:
        logger.error(f"data_collector crashed: {e}", exc_info=True)
        results["agents"]["data_collector"] = {"error": str(e)}

    for name, agent in [
        ("spread_engine",    spread_engine),
        ("neg_risk_engine",  neg_risk_engine),
        ("reversion_engine", reversion_engine),
        ("odds_shift_engine", odds_shift_engine),
    ]:
        try:
            result = agent.run()
            results["agents"][name] = result
            logger.info(f"{name}: {result}")
        except Exception as e:
            logger.error(f"{name} crashed: {e}", exc_info=True)
            results["agents"][name] = {"error": str(e)}

    # Resolve open signals whose window has expired — non-blocking
    try:
        result = outcome_tracker.run()
        results["agents"]["outcome_tracker"] = result
        logger.info(f"outcome_tracker: {result}")
    except Exception as e:
        logger.error(f"outcome_tracker crashed: {e}", exc_info=True)
        results["agents"]["outcome_tracker"] = {"error": str(e)}

    # Check if any watched markets fully resolved — update directional signal outcomes
    try:
        result = hindsight_logger.run()
        results["agents"]["hindsight_logger"] = result
        logger.info(f"hindsight_logger: {result}")
    except Exception as e:
        logger.error(f"hindsight_logger crashed: {e}", exc_info=True)
        results["agents"]["hindsight_logger"] = {"error": str(e)}

    results["ended_at"] = datetime.now(timezone.utc).isoformat()
    _update_signal_streaks(results)
    _print_summary(results)
    return results


def _print_summary(results):
    agents = results.get("agents", {})
    print("\n" + "─" * 50)
    print("  PIPELINE SUMMARY")
    print("─" * 50)

    sc = agents.get("market_scanner", {})
    if sc and "error" not in sc:
        print(f"  Watchlist:  {sc.get('watchlist_size','?')} markets "
              f"from {sc.get('scanned','?')} scanned")

    co = agents.get("data_collector", {})
    if co and "error" not in co:
        print(f"  Snapshots:  {co.get('collected',0)} collected, "
              f"{co.get('failed',0)} failed")

    total = 0
    for engine in ["spread_engine", "neg_risk_engine", "reversion_engine", "odds_shift_engine"]:
        e   = agents.get(engine, {})
        n   = e.get("signals", 0)
        total += n
        top = e.get("top", "—")
        label = engine.replace("_engine","").replace("_","-")
        print(f"  {label:<16} {n} signal(s)" +
              (f"  → {str(top)[:40]}" if top else ""))

    try:
        stats = db.get_db_stats()
        print(f"\n  DB totals:  {stats['markets']} markets | "
              f"{stats['snapshots']} snapshots | "
              f"{stats['signals']} signals")
    except Exception:
        pass

    print(f"  Total signals this run: {total}")

    ot = agents.get("outcome_tracker", {})
    if ot and "error" not in ot:
        print(f"  Outcomes resolved: {ot.get('resolved', 0)} "
              f"(skipped: {ot.get('skipped', 0)})")

    print("─" * 50 + "\n")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-scan", action="store_true")
    args = parser.parse_args()
    run_pipeline(skip_scan=args.skip_scan)
