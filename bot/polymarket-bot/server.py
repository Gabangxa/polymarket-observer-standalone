# server.py — minimal Flask keep-alive for Replit
#
# Replit's "always on" deployment requires an HTTP server to stay alive.
# This runs in a background thread alongside the scheduler.
#
# Endpoints:
#   GET /          — simple alive check (Replit pings this)
#   GET /health    — JSON health status + DB stats
#   GET /signals   — last 24h signals (all strategies)
#   GET /watchlist — current watched markets

import json
import logging
import threading
from datetime import datetime, timezone

from flask import Flask, jsonify

import db

logger = logging.getLogger(__name__)
app = Flask(__name__)


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return "polymarket-bot is running", 200


@app.route("/health")
def health():
    try:
        stats = db.get_db_stats()
        return jsonify({
            "status":    "ok",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "db":        stats,
        })
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return jsonify({"status": "error", "error": str(e)}), 500


@app.route("/signals")
def signals():
    try:
        counts  = db.get_signal_counts()
        recent  = db.get_recent_signals(hours=24, limit=50)
        # Deserialise metadata JSONB
        for s in recent:
            if isinstance(s.get("metadata"), str):
                try:
                    s["metadata"] = json.loads(s["metadata"])
                except Exception:
                    pass
            # Make timestamps serialisable
            for k, v in s.items():
                if hasattr(v, "isoformat"):
                    s[k] = v.isoformat()
        return jsonify({
            "counts": counts,
            "signals": recent,
        })
    except Exception as e:
        logger.error(f"/signals failed: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/watchlist")
def watchlist():
    try:
        markets = db.get_watchlist()
        for m in markets:
            for k, v in m.items():
                if hasattr(v, "isoformat"):
                    m[k] = v.isoformat()
        return jsonify({"markets": markets, "count": len(markets)})
    except Exception as e:
        logger.error(f"/watchlist failed: {e}")
        return jsonify({"error": str(e)}), 500


# ── Execution kill switches ───────────────────────────────────────────────────

@app.route("/execution/status")
def execution_status():
    """Current execution state: pause flag + open order count."""
    try:
        from execution import executor
        paused = executor.is_paused()
    except Exception:
        paused = None   # executor not running

    try:
        open_orders = db.get_open_orders()
        open_count  = len(open_orders)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    return jsonify({
        "paused":      paused,
        "open_orders": open_count,
        "timestamp":   datetime.now(timezone.utc).isoformat(),
    })


@app.route("/execution/pause", methods=["POST"])
def execution_pause():
    """Halt new signal processing. Fill polling continues."""
    try:
        from execution import executor
        executor.pause()
        return jsonify({"status": "paused", "timestamp": datetime.now(timezone.utc).isoformat()})
    except Exception as e:
        logger.error(f"/execution/pause failed: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/execution/resume", methods=["POST"])
def execution_resume():
    """Resume signal processing after a pause."""
    try:
        from execution import executor
        executor.resume()
        return jsonify({"status": "running", "timestamp": datetime.now(timezone.utc).isoformat()})
    except Exception as e:
        logger.error(f"/execution/resume failed: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/execution/cancel-all", methods=["POST"])
def execution_cancel_all():
    """
    Cancel every open CLOB order. Safe to call while paused or running.
    Returns a cancel summary. Requires POLYGON_PRIVATE_KEY to be set.
    """
    try:
        from execution.auth import get_client
        from execution.order_manager import cancel_all_open_orders
        client  = get_client()
        summary = cancel_all_open_orders(client)
        return jsonify({**summary, "timestamp": datetime.now(timezone.utc).isoformat()})
    except RuntimeError as e:
        # get_client() raises RuntimeError if POLYGON_PRIVATE_KEY is missing
        return jsonify({"error": str(e)}), 503
    except Exception as e:
        logger.error(f"/execution/cancel-all failed: {e}")
        return jsonify({"error": str(e)}), 500


# ── Background thread launcher ────────────────────────────────────────────────

def start_server(host: str = "0.0.0.0", port: int = 8080) -> threading.Thread:
    """
    Start Flask in a daemon thread so it doesn't block the scheduler.
    Returns the thread (already started).
    """
    def _run():
        logger.info(f"HTTP server starting on {host}:{port}")
        # Use werkzeug's built-in server; disable reloader in threaded mode
        app.run(host=host, port=port, debug=False, use_reloader=False)

    t = threading.Thread(target=_run, daemon=True, name="http-server")
    t.start()
    logger.info("HTTP server thread started")
    return t
