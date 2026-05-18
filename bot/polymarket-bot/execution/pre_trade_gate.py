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
    EXECUTION_STRATEGIES,
    MAX_SIGNAL_AGE_SECS,
)

logger = logging.getLogger(__name__)


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

    # Gate 2: bankroll configured
    bankroll = db.get_bankroll()
    if bankroll <= 0:
        return False, "Bankroll is not set or zero — configure it in the dashboard"

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

    # Gate 4.5: token-level duplicate — block a second order on the same outcome
    # token even when triggered by a different signal. Catches the scenario where
    # multiple scan-cycle signals fire for the same market before the first order
    # has been recorded as executed (e.g. positions table was empty due to upsert
    # failure, or a reprice created a second signal for the same token).
    token_ids = signal.get("token_ids") or []
    if token_ids and db.order_exists_for_token(token_ids[0]):
        return False, (
            f"live or filled order already exists for token_id={token_ids[0][:16]}… "
            f"— one position per outcome token"
        )

    # Gate 5: total portfolio exposure cap
    approved, reason = _check_portfolio_exposure(bankroll)
    if not approved:
        return False, reason

    # Gate 6: per-position exposure headroom
    if token_ids:
        approved, reason = _check_position_exposure(market_id, token_ids[0], bankroll)
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

    bankroll = db.get_bankroll()
    if bankroll <= 0:
        return False, "Bankroll is not set or zero — configure it in the dashboard"

    approved, reason = _check_portfolio_exposure(bankroll)
    if not approved:
        return False, reason

    token_ids = signal.get("token_ids") or []
    if token_ids:
        approved, reason = _check_position_exposure(market_id, token_ids[0], bankroll)
        if not approved:
            return False, reason

    return True, ""


# ── Shared exposure checks ────────────────────────────────────────────────────

def _check_portfolio_exposure(bankroll: float) -> tuple[bool, str]:
    """Total USDC committed across all open and filled-but-held orders."""
    pct = db.get_max_portfolio_pct()
    cap = Decimal(str(pct)) * Decimal(str(bankroll))
    total = Decimal(str(db.get_total_open_exposure()))
    if total >= cap:
        return False, (
            f"portfolio exposure {total:.2f} USDC >= "
            f"cap {cap:.2f} USDC "
            f"({pct*100:.0f}% of {bankroll} bankroll)"
        )
    return True, ""


def _check_position_exposure(market_id: str, token_id: str, bankroll: float) -> tuple[bool, str]:
    """Per-position cap: filled + working must be below max_position_pct × BANKROLL."""
    pct = db.get_max_position_pct()
    cap = Decimal(str(pct)) * Decimal(str(bankroll))
    position = db.get_position(market_id, token_id, "YES")
    if position:
        total_exposure = (
            Decimal(str(position["total_bought"])) +
            Decimal(str(position["working_buy"]))
        )
        if total_exposure >= cap:
            return False, (
                f"position exposure {total_exposure:.2f} USDC >= "
                f"per-position cap {cap:.2f} USDC "
                f"({pct*100:.0f}% of {bankroll} bankroll)"
            )
    return True, ""
