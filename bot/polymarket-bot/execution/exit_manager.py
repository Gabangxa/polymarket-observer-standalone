# execution/exit_manager.py — NATS-driven dynamic exit manager
#
# Subscribes to pm.snapshots.{market_id} (published by db.insert_snapshot()).
# On each event, evaluates per-strategy exit conditions against the live snapshot.
# Places a SELL GTD order when any condition fires and marks the position
# exit_pending to prevent duplicate orders on back-to-back snapshot events.
#
# Exit conditions by strategy:
#
#   tail_yield_engine:
#     (1) Yield-decay rule — exit when annualised hold-yield drops below
#         TAIL_YIELD_MIN_HOLD_YIELD (10%). Naturally holds through near-expiry
#         (high annualised yield) and exits early when time value has decayed.
#         hold_yield = (1 - price) / price × (8760 / hours_to_expiry)
#     (2) Trailing stop — exit if price retreats TRAIL_PIPS_TAIL (0.5¢) below
#         the running peak. Only activates once peak > avg_cost (in profit).
#
#   spread_engine:
#     (1) Spread-compression rule — exit when live spread < SPREAD_EXIT_FEE_MULTIPLE
#         (1.5×) × estimated fee. Original edge has dissipated.
#     (2) Trailing stop — exit if price retreats TRAIL_PIPS_SPREAD (1¢) below peak.
#         Only activates once peak > avg_cost (in profit).
#
#   neg_risk_overround:
#     No exit. Profit is locked at fill time across all legs. Exiting a single
#     leg mid-stream creates directional exposure on the others. Hold to resolution.
#
# Position index is seeded from DB on start() and refreshed via
# pm.execution.filled.> events so avg_cost / net_shares stay current.
# peak_price and end_date are preserved across refreshes.

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone

import db
import nats_bus
from config import (
    TAIL_YIELD_MIN_HOLD_YIELD,
    SPREAD_EXIT_FEE_MULTIPLE,
    TRAIL_PIPS_TAIL,
    TRAIL_PIPS_SPREAD,
)

logger = logging.getLogger(__name__)


@dataclass
class _PositionEntry:
    market_id:    str
    token_id:     str
    side:         str
    avg_cost:     float
    net_shares:   float
    strategy:     str
    # peak_price: running maximum since entry. Initialised to avg_cost so the
    # trailing stop only activates after the position has shown profit (peak > avg_cost).
    peak_price:   float = field(default=0.0)
    end_date:     datetime | None = field(default=None)
    exit_pending: bool = field(default=False)
    # neg_risk: must match the market's contract type so place_exit_order selects
    # the correct EIP-712 contract address. Sourced from markets.neg_risk at seed time.
    neg_risk:     bool = field(default=False)


_index: dict[str, _PositionEntry] = {}
_lock  = threading.Lock()

_client  = None
_enabled = False


# ── Position index ────────────────────────────────────────────────────────────

def _seed_index() -> int:
    """Load all open positions (with strategy and end_date) into the in-memory index."""
    positions = db.get_open_positions_with_strategy()
    with _lock:
        _index.clear()
        for p in positions:
            net = float(p.get("total_bought") or 0) - float(p.get("total_sold") or 0)
            if net <= 0:
                continue
            avg_cost = float(p.get("avg_cost") or 0)
            _index[p["market_id"]] = _PositionEntry(
                market_id=p["market_id"],
                token_id=p["token_id"],
                side=p.get("side", "YES"),
                avg_cost=avg_cost,
                net_shares=net,
                strategy=p.get("strategy") or "",
                peak_price=avg_cost,   # conservative: assume no upside seen yet
                end_date=p.get("end_date"),
                neg_risk=bool(p.get("neg_risk", False)),
            )
    return len(_index)


# ── Exit evaluators ───────────────────────────────────────────────────────────

def _hours_to_expiry(end_date: datetime | None) -> float:
    """Compute hours until end_date. Returns inf if end_date is unknown."""
    if end_date is None:
        return float("inf")
    now = datetime.now(timezone.utc)
    if end_date.tzinfo is None:
        end_date = end_date.replace(tzinfo=timezone.utc)
    return max((end_date - now).total_seconds() / 3600, 0.1)


def _should_exit_tail_yield(
    entry: _PositionEntry,
    current_price: float,
) -> tuple[bool, str]:
    """
    Trailing stop (checked first — risk management):
      Fire if price has retreated TRAIL_PIPS_TAIL below the running peak,
      but only after peak has exceeded avg_cost (position must first show profit).

    Yield-decay rule:
      Annualised hold-yield = (1 - price) / price × (8760 / hours_to_expiry).
      Exit when yield drops below TAIL_YIELD_MIN_HOLD_YIELD (opportunity-cost floor).
      This naturally holds through near-expiry and exits early when time-value decays.
    """
    # Trailing stop
    if entry.peak_price > entry.avg_cost:
        trail_floor = entry.peak_price - TRAIL_PIPS_TAIL
        if current_price < trail_floor:
            return True, (
                f"trail_stop | price={current_price:.4f} "
                f"< peak={entry.peak_price:.4f} - {TRAIL_PIPS_TAIL}"
            )

    # Yield-decay
    hours = _hours_to_expiry(entry.end_date)
    if hours == float("inf"):
        return False, ""

    remaining_return = (1.0 - current_price) / max(current_price, 0.001)
    annualised_yield = remaining_return * (8760.0 / hours)

    if annualised_yield < TAIL_YIELD_MIN_HOLD_YIELD:
        return True, (
            f"yield_decay | annualised={annualised_yield:.1%} "
            f"< floor={TAIL_YIELD_MIN_HOLD_YIELD:.0%} | hours={hours:.1f}"
        )

    return False, ""


def _should_exit_spread(
    entry: _PositionEntry,
    current_price: float,
    snapshot: dict,
) -> tuple[bool, str]:
    """
    Trailing stop (checked first — risk management):
      Fire if price retreated TRAIL_PIPS_SPREAD below the running peak,
      but only after peak has exceeded avg_cost.

    Spread-compression rule:
      Estimate current round-trip fee: fee ≈ (fee_rate_bps / 10000) × p × (1-p).
      Exit when live spread < SPREAD_EXIT_FEE_MULTIPLE × fee — the original edge
      has compressed to near the cost of trading it.
    """
    # Trailing stop
    if entry.peak_price > entry.avg_cost:
        trail_floor = entry.peak_price - TRAIL_PIPS_SPREAD
        if current_price < trail_floor:
            return True, (
                f"trail_stop | price={current_price:.4f} "
                f"< peak={entry.peak_price:.4f} - {TRAIL_PIPS_SPREAD}"
            )

    # Spread-compression
    raw_spread      = snapshot.get("spread")
    raw_fee_bps     = snapshot.get("fee_rate_bps")
    if raw_spread is None or raw_fee_bps is None:
        return False, ""

    live_spread  = float(raw_spread)
    fee_rate     = float(raw_fee_bps) / 10_000
    # Fee peaks at p=0.5; use current price to stay representative
    estimated_fee = fee_rate * current_price * (1.0 - current_price)

    if estimated_fee <= 0:
        return False, ""

    threshold = SPREAD_EXIT_FEE_MULTIPLE * estimated_fee
    if live_spread < threshold:
        return True, (
            f"spread_compression | spread={live_spread:.4f} "
            f"< {SPREAD_EXIT_FEE_MULTIPLE}× fee={estimated_fee:.4f} "
            f"(threshold={threshold:.4f})"
        )

    return False, ""


# ── NATS callbacks ────────────────────────────────────────────────────────────

def _on_snapshot(subject: str, data: dict) -> None:
    """
    NATS callback: pm.snapshots.{market_id}
    Fired after every snapshot write. Updates peak_price then evaluates
    the exit condition for the market.
    """
    if not _enabled:
        return

    market_id = subject[len("pm.snapshots."):]
    yes_price = data.get("yes_price")
    if yes_price is None:
        return
    current_price = float(yes_price)

    with _lock:
        entry = _index.get(market_id)
        if entry is None or entry.exit_pending:
            return
        # Ratchet peak upward (never let it fall)
        if current_price > entry.peak_price:
            entry.peak_price = current_price
        # Shallow copy for evaluation outside the lock
        snapshot = _PositionEntry(**entry.__dict__)

    # Evaluate exit condition (no lock held during computation)
    strategy     = snapshot.strategy
    should_exit  = False
    reason       = ""

    if strategy == "tail_yield_engine":
        should_exit, reason = _should_exit_tail_yield(snapshot, current_price)

    elif strategy == "spread_engine":
        should_exit, reason = _should_exit_spread(snapshot, current_price, data)

    elif strategy == "neg_risk_overround":
        return  # held to resolution — see module docstring

    if not should_exit:
        return

    # Mark pending before any I/O to prevent re-entry on the next snapshot
    with _lock:
        entry = _index.get(market_id)
        if entry is None or entry.exit_pending:
            return
        entry.exit_pending = True

    logger.info(
        f"Exit triggered | market={market_id} | strategy={strategy} | {reason} "
        f"| avg_cost={snapshot.avg_cost:.4f} | price={current_price:.4f} "
        f"| shares={snapshot.net_shares:.4f}"
    )

    from execution.order_manager import place_exit_order
    try:
        result = place_exit_order(snapshot.__dict__, current_price, _client)
        if result["ok"]:
            logger.info(
                f"Exit order placed | clord_id={result['clord_id']} | market={market_id}"
            )
        else:
            logger.warning(
                f"Exit order failed | market={market_id} | error={result['error']}"
            )
            _reset_exit_pending(market_id)
    except Exception as e:
        logger.error(f"Exit order exception | market={market_id}: {e}", exc_info=True)
        _reset_exit_pending(market_id)


def _on_fill(subject: str, data: dict) -> None:
    """
    NATS callback: pm.execution.filled.{strategy}.{market_id}
    Refreshes the in-memory entry from DB so avg_cost / net_shares stay current.
    Preserves peak_price, end_date, and exit_pending across the refresh.
    """
    parts     = subject.split(".", 4)
    market_id = parts[4] if len(parts) == 5 else None
    if not market_id:
        return

    try:
        positions = db.get_open_positions_with_strategy(market_id=market_id)
        with _lock:
            if not positions:
                _index.pop(market_id, None)
                return
            p   = positions[0]
            net = float(p.get("total_bought") or 0) - float(p.get("total_sold") or 0)
            if net <= 0:
                _index.pop(market_id, None)
                return
            existing      = _index.get(market_id)
            avg_cost      = float(p.get("avg_cost") or 0)
            _index[market_id] = _PositionEntry(
                market_id=p["market_id"],
                token_id=p["token_id"],
                side=p.get("side", "YES"),
                avg_cost=avg_cost,
                net_shares=net,
                strategy=p.get("strategy") or "",
                # Preserve running state; fall back to avg_cost on first-ever entry
                peak_price   = existing.peak_price if existing else avg_cost,
                end_date     = existing.end_date   if existing else p.get("end_date"),
                exit_pending = existing.exit_pending if existing else False,
                neg_risk     = bool(p.get("neg_risk", False)),
            )
    except Exception as e:
        logger.warning(f"Exit manager fill refresh failed | market={market_id}: {e}")


def _reset_exit_pending(market_id: str) -> None:
    with _lock:
        entry = _index.get(market_id)
        if entry:
            entry.exit_pending = False


# ── Startup ───────────────────────────────────────────────────────────────────

def start() -> None:
    """
    Seed the position index and register NATS subscriptions.
    Called from executor.start_executor() before the executor thread starts.

    If the CLOB client cannot be initialised (missing credentials), exit orders
    are disabled — positions will be held to expiry or resolution.
    """
    global _client, _enabled

    try:
        count = _seed_index()
        logger.info(f"Exit manager: seeded {count} open position(s)")
    except Exception as e:
        logger.warning(
            f"Exit manager: DB seed failed ({e}). "
            "Exit orders disabled — run DB migration to enable. "
            "Bot will continue without dynamic exits."
        )
        _enabled = False
        return

    try:
        from execution.auth import get_client
        _client  = get_client()
        _enabled = True
        logger.info("Exit manager: CLOB client ready — dynamic exits active")
    except Exception as e:
        logger.warning(
            f"Exit manager: could not init CLOB client ({e}). "
            "Exit orders disabled — positions held to expiry."
        )
        _enabled = False

    nats_bus.subscribe("pm.snapshots.>",        _on_snapshot)
    nats_bus.subscribe("pm.execution.filled.>", _on_fill)
    logger.info(
        "Exit manager: subscriptions registered "
        "(pm.snapshots.>, pm.execution.filled.>)"
    )
