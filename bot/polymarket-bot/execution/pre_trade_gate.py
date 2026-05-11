# execution/pre_trade_gate.py — pre-trade validation before any order is placed
#
# check()        — full gate for new signals (6 checks, cheapest first)
# check_reprice() — lighter gate for GTD-expired reprices (exposure only)
#
# Returns (True, "") on approval or (False, "<reason>") on rejection.
# Rejected signals are NOT marked executed — they stay eligible for the
# next cycle unless they age out past MAX_SIGNAL_AGE_SECS.

import logging
from datetime import datetime, timezone
from decimal import Decimal

import db
from config import (
    BANKROLL_USDC,
    EXECUTION_STRATEGIES,
    MAX_PORTFOLIO_PCT,
    MAX_POSITION_PCT,
    MAX_SIGNAL_AGE_SECS,
)

logger = logging.getLogger(__name__)

_MAX_EXPOSURE_PER_POSITION = Decimal(str(MAX_POSITION_PCT))  * Decimal(str(BANKROLL_USDC))
_MAX_PORTFOLIO_EXPOSURE    = Decimal(str(MAX_PORTFOLIO_PCT)) * Decimal(str(BANKROLL_USDC))


def check(signal: dict) -> tuple[bool, str]:
    """
    Full pre-trade gate for new signals from get_executable_signals().
    Returns (approved: bool, reason: str).
    """
    signal_id = signal.get("id")
    strategy  = signal.get("strategy", "")
    market_id = signal.get("market_id", "")

    # Gate 1: strategy allowed (no DB)
    if strategy not in EXECUTION_STRATEGIES:
        return False, f"strategy '{strategy}' not in execution allowlist"

    # Gate 2: bankroll configured (no DB)
    if BANKROLL_USDC <= 0:
        return False, "BANKROLL_USDC is not set or zero — cannot size orders"

    # Gate 3: signal freshness (no DB)
    emitted_at = signal.get("emitted_at")
    if emitted_at is not None:
        if isinstance(emitted_at, str):
            emitted_at = datetime.fromisoformat(emitted_at.replace("Z", "+00:00"))
        age_secs = (datetime.now(timezone.utc) - emitted_at).total_seconds()
        if age_secs > MAX_SIGNAL_AGE_SECS:
            return False, f"signal is stale ({age_secs:.0f}s > {MAX_SIGNAL_AGE_SECS}s limit)"

    # Gate 4: idempotency — has this signal already been ordered?
    if signal_id and db.order_exists_for_signal(signal_id):
        return False, f"order already exists for signal_id={signal_id}"

    # Gate 5: total portfolio exposure cap
    approved, reason = _check_portfolio_exposure()
    if not approved:
        return False, reason

    # Gate 6: per-position exposure headroom
    token_ids = signal.get("token_ids") or []
    if token_ids:
        approved, reason = _check_position_exposure(market_id, token_ids[0])
        if not approved:
            return False, reason

    logger.info(
        f"Pre-trade gate APPROVED | signal_id={signal_id} | "
        f"strategy={strategy} | market={market_id}"
    )
    return True, ""


def check_reprice(signal: dict) -> tuple[bool, str]:
    """
    Lighter gate for GTD-expired reprice orders.
    Skips: strategy allowlist, staleness, idempotency (all already validated).
    Checks: bankroll configured, portfolio exposure, per-position exposure.
    """
    market_id = signal.get("market_id", "")

    if BANKROLL_USDC <= 0:
        return False, "BANKROLL_USDC is not set or zero"

    approved, reason = _check_portfolio_exposure()
    if not approved:
        return False, reason

    token_ids = signal.get("token_ids") or []
    if token_ids:
        approved, reason = _check_position_exposure(market_id, token_ids[0])
        if not approved:
            return False, reason

    return True, ""


# ── Shared exposure checks ────────────────────────────────────────────────────

def _check_portfolio_exposure() -> tuple[bool, str]:
    """Total USDC committed across all open and filled-but-held orders."""
    total = Decimal(str(db.get_total_open_exposure()))
    if total >= _MAX_PORTFOLIO_EXPOSURE:
        return False, (
            f"portfolio exposure {total:.2f} USDC >= "
            f"cap {_MAX_PORTFOLIO_EXPOSURE:.2f} USDC "
            f"({MAX_PORTFOLIO_PCT*100:.0f}% of {BANKROLL_USDC} bankroll)"
        )
    return True, ""


def _check_position_exposure(market_id: str, token_id: str) -> tuple[bool, str]:
    """Per-position cap: filled + working must be below MAX_POSITION_PCT × BANKROLL."""
    position = db.get_position(market_id, token_id, "YES")
    if position:
        total_exposure = (
            Decimal(str(position["total_bought"])) +
            Decimal(str(position["working_buy"]))
        )
        if total_exposure >= _MAX_EXPOSURE_PER_POSITION:
            return False, (
                f"position exposure {total_exposure:.2f} USDC >= "
                f"per-position cap {_MAX_EXPOSURE_PER_POSITION:.2f} USDC "
                f"({MAX_POSITION_PCT*100:.0f}% of {BANKROLL_USDC} bankroll)"
            )
    return True, ""
