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

    # Gate 3: signal freshness (no DB) — fail-closed: a missing emitted_at
    # cannot be proven fresh and is rejected, not silently skipped.
    approved, reason = _check_freshness(signal, MAX_SIGNAL_AGE_SECS)
    if not approved:
        return False, reason

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
        token_id = token_ids[0]
        side = _side_for_token(token_ids, token_id)
        approved, reason = _check_position_exposure(market_id, token_id, side)
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
    Skips: strategy allowlist, idempotency (already validated pre-expiry).
    Checks: bankroll configured, freshness (of the driving snapshot — see
    _evaluate_reprice, which stamps `emitted_at` from the snapshot's
    `collected_at`), portfolio exposure, per-position exposure.
    """
    market_id = signal.get("market_id", "")

    if BANKROLL_USDC <= 0:
        return False, "BANKROLL_USDC is not set or zero"

    # Freshness — same fail-closed rule as check()'s Gate 3. A reprice signal
    # carries `emitted_at` derived from its driving snapshot's `collected_at`
    # (set by executor._evaluate_reprice), NOT the original signal's emit
    # time — reprice legitimately re-derives edge from a fresh snapshot, so
    # what must be fresh is the snapshot that drove this reprice.
    approved, reason = _check_freshness(signal, MAX_SIGNAL_AGE_SECS)
    if not approved:
        return False, reason

    approved, reason = _check_portfolio_exposure()
    if not approved:
        return False, reason

    token_ids = signal.get("token_ids") or []
    if token_ids:
        token_id = token_ids[0]
        side = _side_for_token(token_ids, token_id)
        approved, reason = _check_position_exposure(market_id, token_id, side)
        if not approved:
            return False, reason

    return True, ""


# ── Shared exposure / freshness checks ─────────────────────────────────────────

def _check_freshness(signal: dict, max_age_secs: int) -> tuple[bool, str]:
    """
    Signal-freshness gate, fail-closed.

    A signal with no `emitted_at` cannot be proven fresh, so it is REJECTED —
    not silently passed. (Previously `check()` skipped this check entirely
    when `emitted_at` was absent; see PMB-101-102-dependency-pricing-v3.md
    AC-5.) Verified against every current live-strategy signal producer
    (spread_engine, tail_yield_engine, plus the reprice path via
    executor._evaluate_reprice): all reach this gate with `emitted_at`
    populated, so this is a strict correctness fix, not a behavior change,
    for the existing books.
    """
    emitted_at = signal.get("emitted_at")
    if emitted_at is None:
        return False, "signal has no emitted_at — cannot verify freshness (fail-closed reject)"
    if isinstance(emitted_at, str):
        emitted_at = datetime.fromisoformat(emitted_at.replace("Z", "+00:00"))
    age_secs = (datetime.now(timezone.utc) - emitted_at).total_seconds()
    if age_secs > max_age_secs:
        return False, f"signal is stale ({age_secs:.0f}s > {max_age_secs}s limit)"
    return True, ""


def _side_for_token(token_ids: list, token_id: str) -> str:
    """
    Map a token_id to its side label, using the venue's ordering convention:
    token_ids[0] = YES, token_ids[1] = NO (mirrors
    order_manager._get_token_id's index mapping). Today every live strategy
    only ever trades token_ids[0] (YES) — see order_manager.place_order's
    hardcoded `_get_token_id(signal, "BUY")` — so this always resolves to
    "YES" in production right now. It is derived generically (not hardcoded)
    so a future per-leg caller checking token_ids[1] resolves "NO" correctly.
    """
    if token_ids and len(token_ids) > 1 and token_id == token_ids[1]:
        return "NO"
    return "YES"


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


def _check_position_exposure(market_id: str, token_id: str, side: str) -> tuple[bool, str]:
    """
    Per-position cap: filled + working must be below MAX_POSITION_PCT × BANKROLL,
    keyed on the position's ACTUAL (market_id, token_id, side) — not hardcoded
    to "YES". A NO-side position must be checked against its own row, or a
    missing lookup on the wrong side silently falls through to APPROVE
    (PMB-101-102-dependency-pricing-v3.md AC-13). `side` is mandatory (no
    default) so every call site states explicitly which leg it is checking —
    this keeps the helper cleanly callable per-leg for a future two-leg
    dependency trade, without building that extension here.
    """
    position = db.get_position(market_id, token_id, side)
    if position:
        total_exposure = (
            Decimal(str(position["total_bought"])) +
            Decimal(str(position["working_buy"]))
        )
        if total_exposure >= _MAX_EXPOSURE_PER_POSITION:
            return False, (
                f"position exposure {total_exposure:.2f} USDC >= "
                f"per-position cap {_MAX_EXPOSURE_PER_POSITION:.2f} USDC "
                f"({MAX_POSITION_PCT*100:.0f}% of {BANKROLL_USDC} bankroll) "
                f"[side={side}]"
            )
    return True, ""
