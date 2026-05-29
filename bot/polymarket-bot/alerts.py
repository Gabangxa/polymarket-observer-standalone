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

from config import (
    ZERO_SIGNAL_ALERT_COOLDOWN_SECS,
    ORDER_SKIPPED_ALERT_COOLDOWN_SECS,
)

logger = logging.getLogger(__name__)

_WEBHOOK = None

# Crash alert rate-limit: don't fire again within this many seconds
_CRASH_COOLDOWN_SECS = 900  # 15 minutes
_last_crash_alert_at: float = 0.0

# Generic time-based rate limit keyed by an arbitrary string (engine name,
# skip-reason, etc). Keeps repetitive alerts from flooding Discord.
_alert_cooldowns: dict[str, float] = {}


def _should_send(key: str, cooldown_secs: float) -> bool:
    """Return True if `cooldown_secs` have elapsed since this key last sent.

    First call for a given key always returns True. On True it records the
    send time, so callers must only call this when they intend to send.
    """
    now  = time.monotonic()
    last = _alert_cooldowns.get(key, 0.0)
    if now - last < cooldown_secs:
        return False
    _alert_cooldowns[key] = now
    return True


def _webhook() -> str | None:
    global _WEBHOOK
    if _WEBHOOK is None:
        _WEBHOOK = os.environ.get("DISCORD_WEBHOOK_URL", "")
    return _WEBHOOK or None


def check_alerting_configured() -> bool:
    """Log loudly at startup if the alert channel is unconfigured.

    Every _send() silently no-ops when DISCORD_WEBHOOK_URL is unset — which
    means a misconfigured alert channel hides ALL alerts, including the crash
    alerts whose entire job is to make failures visible. That violates the
    fail-loudly contract. Call this once at startup so an unset webhook is
    itself a visible WARNING. Returns True if alerting is configured.
    """
    if _webhook():
        logger.info("Discord alerting configured — alerts will be delivered")
        return True
    logger.warning(
        "DISCORD_WEBHOOK_URL is not set — all alerts (crashes, fills, "
        "cancel-all, drift) will be SILENTLY DROPPED. Set it in the bot "
        "service variables to restore alerting."
    )
    return False


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
            headers={
                "Content-Type": "application/json",
                # Discord REQUIRES a valid User-Agent and rejects the default
                # urllib one ("Python-urllib/x.y") with 403 Forbidden, silently
                # dropping ALL alerts. Documented format is `DiscordBot ($url,
                # $version)`; see https://docs.discord.com/developers/reference
                "User-Agent": "DiscordBot (https://github.com/Gabangxa/polymarket-observer-standalone, 1.0)",
            },
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
    """Alert when an engine hasn't fired a signal for too many consecutive runs.

    Rate-limited per engine: once the streak passes the warn threshold this is
    called every pipeline run (~30s), which floods the channel. Send at most
    once per engine per ZERO_SIGNAL_ALERT_COOLDOWN_SECS.
    """
    if not _should_send(f"zero_signal:{engine}", ZERO_SIGNAL_ALERT_COOLDOWN_SECS):
        return
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


def geoblock_detected(status: dict) -> None:
    """Alert when the deploy region is geoblocked by Polymarket — order
    placement will 403 on every attempt. Rate-limited (hourly) since the
    probe re-runs on a schedule and the condition is sticky until redeploy."""
    if not _should_send("geoblock", ZERO_SIGNAL_ALERT_COOLDOWN_SECS):
        return
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    _send(
        f":no_entry_sign: **GEOBLOCKED — orders will be rejected** | {ts}\n"
        f"Polymarket blocks trading from this region. Every order placement "
        f"will return HTTP 403 until the service runs from a permitted region.\n"
        f"Detected: country=`{status.get('country')}` "
        f"region=`{status.get('region')}` ip=`{status.get('ip')}`"
    )


def order_skipped(
    strategy: str,
    market_id: str,
    question: str,
    reason: str,
    detail: str = "",
) -> None:
    """Alert when a signal could not be turned into an order *before* it ever
    reached the CLOB — size above cap, missing price/token, bankroll not set,
    db error, etc. These are real missed trades but never produce a CLOB
    rejection, so order_rejected() never fires for them.

    Rate-limited per reason so a systemic failure (e.g. every signal missing
    price metadata) can't flood the channel.
    """
    if not _should_send(f"order_skipped:{reason}", ORDER_SKIPPED_ALERT_COOLDOWN_SECS):
        return
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    q  = (question or market_id)[:60]
    line = (
        f":no_entry: **Order skipped (no CLOB order)** | `{strategy}` | {ts}\n"
        f"`{q}`\n"
        f"Reason: `{reason}`"
    )
    if detail:
        line += f" — `{detail[:160]}`"
    _send(line)


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


def reconciler_orphans(orphan_ids: list[str]) -> None:
    """
    Alert when the order reconciler finds CLOB orders the bot did not create.
    Usually means a manual order, a leftover from a prior deployment, or — in
    the worst case — a signature replay. Operator must investigate.
    """
    if not orphan_ids:
        return
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    shown = ", ".join(f"`{oid[:12]}…`" for oid in orphan_ids[:5])
    extra = f" (+{len(orphan_ids) - 5} more)" if len(orphan_ids) > 5 else ""
    _send(
        f":warning: **Orphan CLOB orders detected** | {ts}\n"
        f"{len(orphan_ids)} order(s) on Polymarket are not tracked by the bot: "
        f"{shown}{extra}\n"
        f"Investigate before next cancel-all."
    )


def position_drift(drift_summary: dict) -> None:
    """
    Alert when the position reconciler finds non-trivial drift between the
    DB positions table and the actual Polymarket wallet balance.
    """
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    _send(
        f":warning: **Position drift detected** | {ts}\n"
        f"DB rows: {drift_summary.get('db_rows', '?')} | "
        f"Wallet positions: {drift_summary.get('wallet_rows', '?')} | "
        f"Drift markets: {drift_summary.get('drift_markets', '?')}\n"
        f"Check `/health` and run reconciliation manually."
    )
