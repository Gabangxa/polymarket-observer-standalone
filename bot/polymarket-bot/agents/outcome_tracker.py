# agents/outcome_tracker.py
# Resolves open signals by comparing entry state to current price data.
# Called each pipeline run after the signal engines so open signals are
# gradually closed out as their resolution windows expire.
#
# Resolution windows (configurable):
#   spread_engine/spread_harvesting — 2 hours  (tight MM window)
#   neg_risk_overround              — 6 hours  (over-round persists or tightens intraday)
#   tail_yield_engine               — 6 hours  (interim check; hindsight_logger handles true close)
#
# Neg-risk resolution uses a 3-tier fallback per leg:
#   1. Latest snapshot in the cached batch (zero cost)
#   2. Live midpoint from the Polymarket /midpoint endpoint via api.get_midpoint()
# Token IDs are stored in signal metadata, so resolution works even when the
# leg's market has rotated off the watchlist and its snapshots are gone.
# Signals older than (window + ARCHIVE_GRACE_HOURS) that still cannot be
# resolved are archived with outcome=NULL so they stop polluting the queue.

import json
import logging
from datetime import datetime, timezone

import db
import api as polymarket_api

logger = logging.getLogger(__name__)

RESOLUTION_WINDOWS = {
    "spread_engine":      2,     # current strategy name written by spread_engine.py
    "spread_harvesting":  2,     # legacy name — kept until old signals age out
    "neg_risk_overround": 6,
    "tail_yield_engine":  6,     # interim price-hold check; hindsight_logger handles true close
}

# Signals past (window + ARCHIVE_GRACE_HOURS) that still cannot be resolved
# are marked resolved with outcome=NULL. This drains the backlog of signals
# whose underlying data is permanently gone (e.g., legs whose markets were
# pruned before the protection in db.prune_watchlist was added).
ARCHIVE_GRACE_HOURS = 48

# Per-cycle cap on how many neg-risk signals trigger live API resolution.
# Each signal touches ~N legs × one /midpoint call. 20 signals × ~8 legs ≈
# 160 API calls per cycle — comfortable under Polymarket rate limits.
NEG_RISK_LIVE_RESOLVE_CAP = 20

# Tokens whose orderbook returned 404 are permanently gone (market resolved,
# delisted, or never existed). Cached for the process lifetime so we don't
# hammer the /midpoint endpoint with calls we know will fail. Transient
# failures (timeout / 5xx) do NOT poison the cache.
_DEAD_TOKENS: set[str] = set()


def _resolve_spread(signal: dict, current_snapshot: dict):
    """
    Spread is captured if the market price stayed near entry (spread collected).
    Win  = |exit - entry| <= net_spread * 0.5 (price stable, spread pocketed)
    Loss = price moved more than half the net spread against the position
    PnL  = net_spread on win, -|price_move| on loss
    """
    meta        = signal.get("metadata") or {}
    entry_price = float(signal.get("entry_price") or meta.get("yes_price") or 0)
    net_spread  = float(meta.get("net_spread") or 0)
    exit_price  = float(current_snapshot.get("yes_price") or entry_price)

    price_move = abs(exit_price - entry_price)
    outcome    = price_move <= net_spread * 0.5
    pnl        = net_spread if outcome else -price_move
    return outcome, round(exit_price, 4), round(pnl, 6)


def _resolve_tail_yield(signal: dict, current_snapshot: dict):
    """
    Tail yield wins if the YES price held or improved after entry.
    Entry = current_price stored in signal metadata.
    Win  = exit_price >= entry_price (near-certainty held or compressed further)
    Loss = exit_price < entry_price (probability collapsed)
    PnL  = exit_price - entry_price
    Note: hindsight_logger supersedes this with the true resolution when the market closes.
    """
    meta        = signal.get("metadata") or {}
    entry_price = float(meta.get("yes_price") or meta.get("current_price") or signal.get("entry_price") or 0)
    exit_price  = float(current_snapshot.get("yes_price") or entry_price)
    outcome     = exit_price >= entry_price
    pnl         = round(exit_price - entry_price, 6)
    return outcome, round(exit_price, 4), pnl


def _resolve_neg_risk(
    signal: dict,
    snapshots_by_market: dict,
    allow_live_api: bool = True,
) -> tuple[bool | None, float | None, float | None, bool]:
    """
    Neg-risk wins if the over-round has decreased since signal time.
    Re-sums yes_prices for all outcome markets in the event.

    Price source per leg (in priority order):
      1. snapshots_by_market[market_id] — cached batch, zero cost
      2. polymarket_api.get_midpoint_status(yes_token_id) — live API call,
         skipped entirely if the token is already in _DEAD_TOKENS

    The resolution requires every leg to have a current price. A single
    missing leg makes the sum incomplete — signals are NOT resolved against
    partial data (would produce phantom WIN/LOSS outcomes).

    Returns (outcome, exit_price, pnl, has_dead_leg). When has_dead_leg is
    True the caller should archive the signal immediately rather than
    waiting the full grace window — at least one leg's market is gone for
    good and the signal cannot resolve via this path.

    allow_live_api: pass False to skip API calls (used for the rate-limited
    batch of cached-only resolutions before falling through to live).
    """
    meta      = signal.get("metadata") or {}
    entry_sum = float(meta.get("sum_prices") or meta.get("sum_asks") or 1.0)
    outcomes  = meta.get("outcomes") or []

    if not outcomes:
        return None, None, None, False

    current_prices = []
    has_dead_leg = False
    for outcome_item in outcomes:
        mid = outcome_item.get("market_id")
        price = None

        # Tier 1: cached snapshot batch
        if mid and mid in snapshots_by_market:
            snap = snapshots_by_market[mid]
            raw = snap.get("yes_price")
            if raw is not None:
                price = float(raw)

        # Tier 2: live API midpoint via the leg's yes_token_id from metadata.
        # Skip if we've already confirmed this token's orderbook is gone.
        if price is None and allow_live_api:
            token_id = outcome_item.get("yes_token_id")
            if token_id and token_id in _DEAD_TOKENS:
                has_dead_leg = True
                return None, None, None, True
            if token_id:
                live, is_gone = polymarket_api.get_midpoint_status(token_id)
                if is_gone:
                    _DEAD_TOKENS.add(token_id)
                    has_dead_leg = True
                    return None, None, None, True
                if live is not None and live > 0:
                    price = float(live)

        if price is None:
            return None, None, None, False
        current_prices.append(price)

    current_sum = sum(current_prices)
    outcome     = current_sum < entry_sum                   # over-round tightened = win
    pnl         = round(entry_sum - current_sum, 6)         # fractional price units, same as other strategies
    exit_price  = round(current_sum, 6)
    return outcome, exit_price, pnl, False


def run():
    logger.info("=== Outcome tracker starting ===")

    # Pull latest snapshot per market once — reused for all strategies
    latest_snapshots    = db.get_latest_snapshots(limit=500)
    snapshots_by_market = {s["market_id"]: s for s in latest_snapshots}

    resolved_total = 0
    archived_total = 0
    # Per-reason skip counters. A growing backlog with all skips in one bucket
    # tells you exactly which assumption is broken (snapshot gap vs. leg-data gap vs. exception).
    skip_reasons: dict[str, int] = {
        "no_market_id":      0,
        "no_snapshot":       0,
        "no_leg_prices":     0,   # neg_risk: legs unresolvable even via live API
        "unknown_strategy":  0,
        "exception":         0,
    }

    now = datetime.now(timezone.utc)
    neg_risk_live_budget = NEG_RISK_LIVE_RESOLVE_CAP

    for strategy, window_hours in RESOLUTION_WINDOWS.items():
        signals = db.get_unresolved_signals(strategy=strategy, older_than_hours=window_hours)
        if not signals:
            continue

        logger.info(f"  {strategy}: {len(signals)} unresolved signal(s) past {window_hours}h window")

        for signal in signals:
            try:
                market_id = signal.get("market_id")

                if strategy in ("spread_engine", "spread_harvesting"):
                    if not market_id:
                        skip_reasons["no_market_id"] += 1
                        continue
                    if market_id not in snapshots_by_market:
                        skip_reasons["no_snapshot"] += 1
                        continue
                    outcome, exit_price, pnl = _resolve_spread(
                        signal, snapshots_by_market[market_id]
                    )

                elif strategy == "neg_risk_overround":
                    # First try cached-only resolution (zero cost).
                    outcome, exit_price, pnl, has_dead_leg = _resolve_neg_risk(
                        signal, snapshots_by_market, allow_live_api=False
                    )
                    # Fall through to live API resolution within the per-cycle budget.
                    if outcome is None and not has_dead_leg and neg_risk_live_budget > 0:
                        neg_risk_live_budget -= 1
                        outcome, exit_price, pnl, has_dead_leg = _resolve_neg_risk(
                            signal, snapshots_by_market, allow_live_api=True
                        )
                    if outcome is None:
                        # Archive early if any leg's market is confirmed gone
                        # (404 on midpoint) — the signal cannot resolve via this
                        # path, no need to wait the full grace window.
                        if has_dead_leg:
                            db.archive_signal(signal["id"])
                            archived_total += 1
                            continue
                        # Standard archive path: past resolution window + grace.
                        emitted_at = signal.get("emitted_at")
                        if emitted_at is not None:
                            age_hours = (now - emitted_at).total_seconds() / 3600
                            if age_hours > (window_hours + ARCHIVE_GRACE_HOURS):
                                db.archive_signal(signal["id"])
                                archived_total += 1
                                continue
                        skip_reasons["no_leg_prices"] += 1
                        continue

                elif strategy == "tail_yield_engine":
                    if not market_id:
                        skip_reasons["no_market_id"] += 1
                        continue
                    if market_id not in snapshots_by_market:
                        skip_reasons["no_snapshot"] += 1
                        continue
                    outcome, exit_price, pnl = _resolve_tail_yield(
                        signal, snapshots_by_market[market_id]
                    )

                else:
                    skip_reasons["unknown_strategy"] += 1
                    continue

                db.update_signal_outcome(signal["id"], exit_price, pnl, outcome)
                resolved_total += 1
                logger.info(
                    f"    [{signal['id']}] {strategy} → "
                    f"{'WIN' if outcome else 'LOSS'} | "
                    f"exit={exit_price} | pnl={pnl:+.4f}"
                )

            except Exception as e:
                logger.warning(f"    Failed to resolve signal {signal['id']}: {e}")
                skip_reasons["exception"] += 1

    skipped_total = sum(skip_reasons.values())
    skip_breakdown = ", ".join(f"{k}={v}" for k, v in skip_reasons.items() if v > 0) or "none"
    logger.info(
        f"Outcome tracker done — resolved: {resolved_total}, "
        f"archived: {archived_total}, "
        f"skipped: {skipped_total} ({skip_breakdown})"
    )
    return {
        "agent":         "outcome_tracker",
        "resolved":      resolved_total,
        "archived":      archived_total,
        "skipped":       skipped_total,
        "skip_reasons":  skip_reasons,
    }
