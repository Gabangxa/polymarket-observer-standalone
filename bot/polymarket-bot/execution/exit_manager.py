# execution/exit_manager.py — NATS-driven take-profit exit for open positions
#
# Subscribes to pm.snapshots.{market_id} (published by db.insert_snapshot()).
# On each event, checks the in-memory position index for that market.
# When a take-profit condition is met, places a SELL GTD order and marks the
# position exit_pending to prevent duplicate orders on subsequent snapshot events.
#
# Position index is seeded from DB on start() and refreshed via
# pm.execution.filled.> events so avg_cost and net_shares stay current.
#
# Exit rules by strategy:
#   tail_yield_engine : price >= avg_cost + TAIL_YIELD_TP_RATIO × (1 - avg_cost)
#   spread_engine     : price >= avg_cost + SPREAD_TP_PIPS
#   neg_risk, others  : no exit rule — held to expiry

import logging
import threading
from dataclasses import dataclass, field

import db
import nats_bus
from config import TAIL_YIELD_TP_RATIO, SPREAD_TP_PIPS

logger = logging.getLogger(__name__)


@dataclass
class _PositionEntry:
    market_id:    str
    token_id:     str
    side:         str
    avg_cost:     float
    net_shares:   float
    strategy:     str
    exit_pending: bool = field(default=False)


_index: dict[str, _PositionEntry] = {}
_lock  = threading.Lock()

_client = None
_enabled = False


def _seed_index() -> int:
    """Load all open positions (with originating strategy) into the in-memory index."""
    positions = db.get_open_positions_with_strategy()
    with _lock:
        _index.clear()
        for p in positions:
            net = float(p.get("total_bought") or 0) - float(p.get("total_sold") or 0)
            if net <= 0:
                continue
            _index[p["market_id"]] = _PositionEntry(
                market_id=p["market_id"],
                token_id=p["token_id"],
                side=p.get("side", "YES"),
                avg_cost=float(p.get("avg_cost") or 0),
                net_shares=net,
                strategy=p.get("strategy") or "",
            )
    return len(_index)


def _should_exit(entry: _PositionEntry, current_price: float) -> bool:
    """Return True when the take-profit threshold for this strategy is crossed."""
    if entry.avg_cost <= 0:
        return False

    if entry.strategy == "tail_yield_engine":
        target = entry.avg_cost + TAIL_YIELD_TP_RATIO * (1.0 - entry.avg_cost)
        return current_price >= target

    if entry.strategy == "spread_engine":
        target = entry.avg_cost + SPREAD_TP_PIPS
        return current_price >= target

    return False


def _on_snapshot(subject: str, data: dict) -> None:
    """
    NATS callback: pm.snapshots.{market_id}
    Fires after every snapshot write. Evaluates the TP condition for the market;
    places an exit order when triggered.
    """
    if not _enabled:
        return

    market_id = subject[len("pm.snapshots."):]

    with _lock:
        entry = _index.get(market_id)
        if entry is None or entry.exit_pending:
            return
        # Shallow copy to read state outside the lock (I/O happens below)
        snapshot = _PositionEntry(**entry.__dict__)

    yes_price = data.get("yes_price")
    if yes_price is None:
        return

    current_price = float(yes_price)
    if not _should_exit(snapshot, current_price):
        return

    # Mark pending before any I/O — prevents re-entry from the next snapshot event
    with _lock:
        entry = _index.get(market_id)
        if entry is None or entry.exit_pending:
            return
        entry.exit_pending = True

    logger.info(
        f"Exit triggered | market={market_id} | strategy={snapshot.strategy} "
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
    Refreshes the in-memory entry from DB so avg_cost / net_shares stay current
    after partial fills or new buy entries.
    """
    # subject parts: pm . execution . filled . {strategy} . {market_id}
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
            exit_pending  = existing.exit_pending if existing else False
            _index[market_id] = _PositionEntry(
                market_id=p["market_id"],
                token_id=p["token_id"],
                side=p.get("side", "YES"),
                avg_cost=float(p.get("avg_cost") or 0),
                net_shares=net,
                strategy=p.get("strategy") or "",
                exit_pending=exit_pending,
            )
    except Exception as e:
        logger.warning(f"Exit manager fill refresh failed | market={market_id}: {e}")


def _reset_exit_pending(market_id: str) -> None:
    with _lock:
        entry = _index.get(market_id)
        if entry:
            entry.exit_pending = False


def start() -> None:
    """
    Seed the position index and register NATS subscriptions.
    Called from executor.start_executor() before the executor thread starts.

    If the CLOB client cannot be initialised (missing credentials), exit orders
    are disabled for the session — positions continue to be held to expiry.
    """
    global _client, _enabled

    count = _seed_index()
    logger.info(f"Exit manager: seeded {count} open position(s)")

    try:
        from execution.auth import get_client
        _client  = get_client()
        _enabled = True
        logger.info("Exit manager: CLOB client ready — take-profit exits active")
    except Exception as e:
        logger.warning(
            f"Exit manager: could not init CLOB client ({e}). "
            "Exit orders disabled — positions will be held to expiry."
        )
        _enabled = False

    nats_bus.subscribe("pm.snapshots.>",        _on_snapshot)
    nats_bus.subscribe("pm.execution.filled.>", _on_fill)
    logger.info("Exit manager: NATS subscriptions registered (pm.snapshots.>, pm.execution.filled.>)")
