# server.py — minimal Flask keep-alive for Replit
#
# Replit's "always on" deployment requires an HTTP server to stay alive.
# This runs in a background thread alongside the scheduler.
#
# Endpoints:
#   GET  /                   — simple alive check (Replit pings this)
#   GET  /health             — JSON health status + DB stats
#   GET  /signals            — last 24h signals (all strategies)
#   GET  /watchlist          — current watched markets
#   GET  /logs               — tail today's log file (?lines=N &level=ERROR &date=YYYY-MM-DD) (requires X-API-Key: BOT_API_KEY)
#   POST /execution/pause    — halt signal processing  (requires X-API-Key: BOT_API_KEY)
#   POST /execution/resume   — resume signal processing (requires X-API-Key: BOT_API_KEY)
#   POST /execution/cancel-all — cancel all open orders (requires X-API-Key: BOT_API_KEY)

import hmac
import json
import logging
import os
import threading
from datetime import datetime, timezone

from flask import Flask, jsonify, request

import db

logger = logging.getLogger(__name__)
app = Flask(__name__)


# ── Auth ──────────────────────────────────────────────────────────────────────

def _require_api_key():
    """
    Returns a (response, status) tuple if the request fails auth, or None if OK.
    Reads BOT_API_KEY from env. If unset, all requests are rejected (fail-closed).
    Uses hmac.compare_digest to prevent timing oracle attacks.
    """
    configured = os.environ.get("BOT_API_KEY", "")
    if not configured:
        logger.error("BOT_API_KEY is not set — execution endpoints are locked")
        return jsonify({"error": "Server misconfiguration: BOT_API_KEY not set"}), 503
    provided = request.headers.get("X-API-Key", "")
    if not hmac.compare_digest(provided, configured):
        return jsonify({"error": "Unauthorized"}), 401
    return None


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


@app.route("/logs")
def logs():
    err = _require_api_key()
    if err:
        return err

    try:
        n_lines = int(request.args.get("lines", 200))
        if n_lines < 1 or n_lines > 5000:
            return "lines must be between 1 and 5000", 400
    except ValueError:
        return "lines must be an integer", 400

    date_str = request.args.get("date", "")
    if date_str:
        try:
            datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            return "date must be YYYY-MM-DD", 400
        log_date = date_str
    else:
        log_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    log_path = os.path.join("logs", f"run_{log_date}.log")
    if not os.path.exists(log_path):
        return f"No log file for {log_date}", 404

    with open(log_path, "r") as f:
        all_lines = f.readlines()

    level_filter = request.args.get("level", "").upper()
    if level_filter:
        all_lines = [l for l in all_lines if f"[{level_filter}]" in l]

    tail = all_lines[-n_lines:]
    return "".join(tail), 200, {"Content-Type": "text/plain; charset=utf-8"}


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
    err = _require_api_key()
    if err:
        return err
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
    err = _require_api_key()
    if err:
        return err
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
    err = _require_api_key()
    if err:
        return err
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
