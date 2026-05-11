# alerts.py — Discord notification helper
#
# Set DISCORD_WEBHOOK_URL in environment variables.
# All functions are fire-and-forget: failures are logged but never raise.

import json
import logging
import os
import time
import urllib.request
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_WEBHOOK = None

# Crash alert rate-limit: don't fire again within this many seconds
_CRASH_COOLDOWN_SECS = 900  # 15 minutes
_last_crash_alert_at: float = 0.0


def _webhook() -> str | None:
    global _WEBHOOK
    if _WEBHOOK is None:
        _WEBHOOK = os.environ.get("DISCORD_WEBHOOK_URL", "")
    return _WEBHOOK or None


def _send(content: str) -> None:
    """POST a message to the Discord webhook. Silent on failure."""
    url = _webhook()
    if not url:
        return
    try:
        payload = json.dumps({"content": content}).encode()
        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5):
            pass
    except Exception as e:
        logger.warning(f"Discord alert failed (non-fatal): {e}")


# ── Public alert types ────────────────────────────────────────────────────────

def pipeline_crashed(run_number: int, error: Exception) -> None:
    """Alert when a full pipeline run raises an unhandled exception.
    Rate-limited to once per _CRASH_COOLDOWN_SECS to prevent spam on sustained failure."""
    global _last_crash_alert_at
    now = time.monotonic()
    if now - _last_crash_alert_at < _CRASH_COOLDOWN_SECS:
        logger.debug("Crash alert suppressed (cooldown active)")
        return
    _last_crash_alert_at = now
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    _send(
        f":red_circle: **PolyBot pipeline crashed** | Run #{run_number} | {ts}\n"
        f"```{type(error).__name__}: {str(error)[:300]}```"
    )


def zero_signal_streak(engine: str, streak: int, last_signal_at: str | None,
                       poll_interval_secs: int = 30) -> None:
    """Alert when an engine hasn't fired a signal for too many consecutive runs."""
    ts   = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    last = last_signal_at or "never"
    elapsed_min = streak * poll_interval_secs // 60
    _send(
        f":warning: **Zero-signal streak** | `{engine}` | {ts}\n"
        f"{streak} consecutive runs (~{elapsed_min} min) with no signals.\n"
        f"Last signal: `{last}`"
    )


# ── Execution alerts ──────────────────────────────────────────────────────────

def order_placed(
    strategy: str,
    market_id: str,
    question: str,
    clord_id: str,
    price: float,
    size_usdc: float,
) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    q  = (question or market_id)[:60]
    _send(
        f":green_circle: **Order placed** | `{strategy}` | {ts}\n"
        f"`{q}`\n"
        f"YES @ {price:.3f} | Size: ${size_usdc:.2f} USDC | `{clord_id}`"
    )


def order_filled(
    strategy: str,
    market_id: str,
    clord_id: str,
    filled_qty: float,
    fill_price: float,
) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    _send(
        f":white_check_mark: **Order filled** | `{strategy}` | {ts}\n"
        f"Market: `{market_id[:40]}`\n"
        f"Filled: {filled_qty:.4f} shares @ {fill_price:.3f} | `{clord_id}`"
    )


def order_rejected(
    strategy: str,
    market_id: str,
    question: str,
    clord_id: str,
    error: str,
) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    q  = (question or market_id)[:60]
    _send(
        f":red_circle: **Order rejected** | `{strategy}` | {ts}\n"
        f"`{q}`\n"
        f"Error: `{str(error)[:200]}` | `{clord_id}`"
    )


def executor_paused() -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    _send(
        f":pause_button: **Executor PAUSED** | {ts}\n"
        f"New signal processing halted. Fill polling continues."
    )


def executor_resumed() -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    _send(f":arrow_forward: **Executor RESUMED** | {ts} — signal processing active.")


def cancel_all_fired(summary: dict) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    _send(
        f":stop_sign: **Cancel-all executed** | {ts}\n"
        f"Attempted: {summary['attempted']} | "
        f"Succeeded: {summary['succeeded']} | "
        f"Failed: {summary['failed']} | "
        f"DB-only: {summary['db_only']}"
    )
