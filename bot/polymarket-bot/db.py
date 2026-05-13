# db.py — Postgres connection, schema, and query helpers
#
# Requires DATABASE_URL environment variable:
#   postgresql://user:pass@host:5432/dbname
#
# Schema:
#   markets    — one row per watched market (upserted each scan)
#   snapshots  — one row per market per collection run
#   signals    — one row per signal emitted by any strategy engine

import atexit
import json
import logging
import os
import threading
from contextlib import contextmanager
from datetime import datetime, timezone

import psycopg2
import psycopg2.extras
import psycopg2.pool
from config import MAX_WATCHLIST_SIZE, SNAPSHOT_RETENTION_DAYS

logger = logging.getLogger(__name__)

# ── Connection pool ───────────────────────────────────────────────────────────
# ThreadedConnectionPool is required because two threads share the DB:
#   - main thread  : pipeline (scheduler → engines → outcome_tracker)
#   - daemon thread: Flask keep-alive server (health, signals, watchlist endpoints)
#
# Pool sizing: each thread holds at most 1 connection at a time (queries are
# sequential within each thread), so 2 would suffice. 4 gives headroom for
# brief overlap during collection + signal resolution + simultaneous HTTP hit.

_POOL_MIN = 1
_POOL_MAX = 4

_pool: psycopg2.pool.ThreadedConnectionPool | None = None
_pool_lock = threading.Lock()


def _get_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL environment variable not set. "
            "Set it to a PostgreSQL connection string: "
            "postgresql://user:pass@host:5432/dbname"
        )
    return url


def _get_pool() -> psycopg2.pool.ThreadedConnectionPool:
    """Return the shared connection pool, initialising it on first call."""
    global _pool
    if _pool is not None:
        return _pool
    with _pool_lock:
        if _pool is not None:        # double-checked locking
            return _pool
        _pool = psycopg2.pool.ThreadedConnectionPool(
            _POOL_MIN,
            _POOL_MAX,
            _get_url(),
            cursor_factory=psycopg2.extras.RealDictCursor,
        )
        logger.info(f"DB connection pool ready (min={_POOL_MIN}, max={_POOL_MAX})")
        return _pool


def _close_pool() -> None:
    """Drain the pool on process exit. Registered via atexit."""
    global _pool
    if _pool is not None:
        try:
            _pool.closeall()
        except Exception:
            pass
        _pool = None


atexit.register(_close_pool)


@contextmanager
def get_conn():
    """Borrow a connection from the pool. Auto-commits on clean exit,
    rolls back on error. Discards the connection if rollback itself fails
    (indicates a dead connection that should not be returned to the pool)."""
    pool    = _get_pool()
    conn    = pool.getconn()
    discard = False
    try:
        yield conn
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            discard = True   # connection is dead; don't recycle it
        raise
    finally:
        pool.putconn(conn, close=discard)


# ── Schema ────────────────────────────────────────────────────────────────────

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS markets (
    market_id       TEXT PRIMARY KEY,
    condition_id    TEXT,
    question        TEXT,
    event_title     TEXT,
    event_slug      TEXT,
    tags            TEXT[],
    neg_risk        BOOLEAN DEFAULT FALSE,
    token_ids       TEXT[],
    outcomes        TEXT[],
    volume_24h      NUMERIC,
    liquidity       NUMERIC,
    end_date        TIMESTAMPTZ,
    hours_to_close  NUMERIC,
    fees_enabled    BOOLEAN DEFAULT FALSE,
    score           NUMERIC,
    added_at        TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS snapshots (
    id              BIGSERIAL PRIMARY KEY,
    market_id       TEXT NOT NULL,
    collected_at    TIMESTAMPTZ DEFAULT NOW(),
    yes_price       NUMERIC,
    no_price        NUMERIC,
    spread          NUMERIC,
    midpoint        NUMERIC,
    fee_rate_bps    NUMERIC,
    open_interest   NUMERIC,
    price_history   JSONB,
    top_holders     JSONB,
    recent_trades   JSONB,
    errors          TEXT[],
    yes_ask         NUMERIC,
    no_ask          NUMERIC
);

CREATE INDEX IF NOT EXISTS snapshots_market_collected
    ON snapshots (market_id, collected_at DESC);

CREATE TABLE IF NOT EXISTS signals (
    id              BIGSERIAL PRIMARY KEY,
    strategy        TEXT NOT NULL,
    market_id       TEXT,
    event_slug      TEXT,
    signal_score    NUMERIC,
    metadata        JSONB,
    emitted_at      TIMESTAMPTZ DEFAULT NOW(),
    -- Phase 4: paper trade fields (null until tracked)
    entry_price     NUMERIC,
    exit_price      NUMERIC,
    pnl             NUMERIC,
    resolved        BOOLEAN DEFAULT FALSE,
    outcome         BOOLEAN
);

CREATE INDEX IF NOT EXISTS signals_strategy_emitted
    ON signals (strategy, emitted_at DESC);

CREATE INDEX IF NOT EXISTS signals_market_emitted
    ON signals (market_id, emitted_at DESC);

-- Migrations: add columns to existing tables
ALTER TABLE signals ADD COLUMN IF NOT EXISTS outcome BOOLEAN;
ALTER TABLE markets ADD COLUMN IF NOT EXISTS category TEXT;
ALTER TABLE snapshots ADD COLUMN IF NOT EXISTS yes_ask NUMERIC;
ALTER TABLE snapshots ADD COLUMN IF NOT EXISTS no_ask NUMERIC;

-- Dedup constraint: one signal per (strategy, market/event, clock-hour).
-- COALESCE(market_id, event_slug) covers both per-market signals (market_id set)
-- and per-event signals like neg_risk_overround (market_id NULL, event_slug set).
-- This replaces the application-level check+insert two-connection pattern with a
-- single atomic INSERT ... ON CONFLICT DO NOTHING.
CREATE UNIQUE INDEX IF NOT EXISTS signals_dedup_hourly
    ON signals (strategy, COALESCE(market_id, event_slug), date_trunc('hour', emitted_at));

-- Execution tracking
ALTER TABLE signals ADD COLUMN IF NOT EXISTS executed BOOLEAN DEFAULT FALSE;

CREATE INDEX IF NOT EXISTS signals_executable
    ON signals (executed, signal_score, emitted_at)
    WHERE executed = FALSE;

CREATE TABLE IF NOT EXISTS orders (
    id                BIGSERIAL PRIMARY KEY,
    clord_id          TEXT UNIQUE NOT NULL,
    signal_id         BIGINT REFERENCES signals(id),
    market_id         TEXT NOT NULL,
    token_id          TEXT NOT NULL,
    side              TEXT NOT NULL,
    price             NUMERIC NOT NULL,
    size_usdc         NUMERIC NOT NULL,
    strategy          TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'PENDING_SUBMISSION',
    exchange_order_id TEXT,
    working_qty       NUMERIC DEFAULT 0,
    filled_qty        NUMERIC DEFAULT 0,
    fill_price        NUMERIC,
    submitted_at      TIMESTAMPTZ,
    filled_at         TIMESTAMPTZ,
    canceled_at       TIMESTAMPTZ,
    error_msg         TEXT,
    created_at        TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS orders_status
    ON orders (status, created_at DESC);

CREATE INDEX IF NOT EXISTS orders_signal
    ON orders (signal_id);

-- GTD and reprice tracking (added with GTD/reprice implementation)
ALTER TABLE orders ADD COLUMN IF NOT EXISTS expiration_ts  TIMESTAMPTZ;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS reprice_of     BIGINT REFERENCES orders(id);
ALTER TABLE orders ADD COLUMN IF NOT EXISTS repriced       BOOLEAN DEFAULT FALSE;

CREATE INDEX IF NOT EXISTS orders_reprice_candidates
    ON orders (status, repriced, expiration_ts)
    WHERE status = 'CANCELED' AND repriced = FALSE;

CREATE TABLE IF NOT EXISTS positions (
    id            BIGSERIAL PRIMARY KEY,
    market_id     TEXT NOT NULL,
    token_id      TEXT NOT NULL,
    side          TEXT NOT NULL,
    total_bought  NUMERIC DEFAULT 0,
    total_sold    NUMERIC DEFAULT 0,
    working_buy   NUMERIC DEFAULT 0,
    working_sell  NUMERIC DEFAULT 0,
    avg_cost      NUMERIC,
    pnl_realized  NUMERIC DEFAULT 0,
    pnl_open      NUMERIC DEFAULT 0,
    last_updated  TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (market_id, token_id, side)
);
"""


def init_schema() -> None:
    """Create tables if they don't exist. Safe to call on every startup."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_SQL)
    logger.info("Database schema ready")


# ── markets table ─────────────────────────────────────────────────────────────

def upsert_markets(watchlist: list[dict]) -> int:
    """
    Insert or update markets from a fresh watchlist.
    Returns number of rows upserted.
    """
    if not watchlist:
        return 0

    sql = """
        INSERT INTO markets (
            market_id, condition_id, question, event_title, event_slug,
            tags, category, neg_risk, token_ids, outcomes,
            volume_24h, liquidity, end_date, hours_to_close,
            fees_enabled, score, added_at, updated_at
        ) VALUES (
            %(market_id)s, %(condition_id)s, %(question)s, %(event_title)s, %(event_slug)s,
            %(tags)s, %(category)s, %(neg_risk)s, %(token_ids)s, %(outcomes)s,
            %(volume_24h)s, %(liquidity)s, %(end_date)s, %(hours_to_close)s,
            %(fees_enabled)s, %(score)s, NOW(), NOW()
        )
        ON CONFLICT (market_id) DO UPDATE SET
            question        = EXCLUDED.question,
            event_title     = EXCLUDED.event_title,
            tags            = EXCLUDED.tags,
            category        = EXCLUDED.category,
            neg_risk        = EXCLUDED.neg_risk,
            token_ids       = EXCLUDED.token_ids,
            volume_24h      = EXCLUDED.volume_24h,
            liquidity       = EXCLUDED.liquidity,
            end_date        = EXCLUDED.end_date,
            hours_to_close  = EXCLUDED.hours_to_close,
            fees_enabled    = EXCLUDED.fees_enabled,
            score           = EXCLUDED.score,
            updated_at      = NOW()
    """

    with get_conn() as conn:
        with conn.cursor() as cur:
            psycopg2.extras.execute_batch(cur, sql, watchlist)
    return len(watchlist)


def get_watchlist() -> list[dict]:
    """Return active watchlist markets ordered by score, capped at MAX_WATCHLIST_SIZE."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT * FROM markets
                ORDER BY score DESC NULLS LAST
                LIMIT %s
            """, (MAX_WATCHLIST_SIZE,))
            return [dict(r) for r in cur.fetchall()]


def prune_watchlist(keep_ids: list[str]) -> int:
    """
    Delete markets (and their snapshots and signals) whose market_id is not in keep_ids.
    Called after each scanner run to enforce the watchlist cap.

    Safety: returns 0 immediately if keep_ids is empty — never wipes the
    entire DB due to an upstream API failure returning an empty list.
    Returns the number of markets removed.
    """
    if not keep_ids:
        return 0
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM signals WHERE market_id IS NOT NULL AND NOT (market_id = ANY(%s))",
                (keep_ids,),
            )
            signal_count = cur.rowcount
            cur.execute(
                "DELETE FROM snapshots WHERE NOT (market_id = ANY(%s))",
                (keep_ids,),
            )
            snap_count = cur.rowcount
            cur.execute(
                "DELETE FROM markets WHERE NOT (market_id = ANY(%s))",
                (keep_ids,),
            )
            market_count = cur.rowcount
    if market_count:
        logger.info(
            f"Watchlist pruned: removed {market_count} market(s), "
            f"{snap_count} snapshot(s), {signal_count} signal(s)"
        )
    return market_count


# ── snapshots table ───────────────────────────────────────────────────────────

def insert_snapshot(snapshot: dict) -> int:
    """Insert one snapshot row. Returns the new row id."""
    sql = """
        INSERT INTO snapshots (
            market_id, collected_at,
            yes_price, no_price, spread, midpoint, fee_rate_bps,
            open_interest, price_history, top_holders, recent_trades, errors,
            yes_ask, no_ask
        ) VALUES (
            %(market_id)s, %(collected_at)s,
            %(yes_price)s, %(no_price)s, %(spread)s, %(midpoint)s, %(fee_rate_bps)s,
            %(open_interest)s, %(price_history)s, %(top_holders)s, %(recent_trades)s, %(errors)s,
            %(yes_ask)s, %(no_ask)s
        )
        RETURNING id
    """
    # Serialise JSONB fields
    row = dict(snapshot)
    for field in ("price_history", "top_holders", "recent_trades"):
        val = row.get(field)
        if val is not None and not isinstance(val, str):
            row[field] = json.dumps(val)

    # Serialise open_interest (may be a dict or list from the API)
    oi = row.get("open_interest")
    if isinstance(oi, list):
        # Data API returns [{"market": "GLOBAL", "value": ...}]
        try:
            row["open_interest"] = float(oi[0]["value"]) if oi else None
        except (IndexError, KeyError, TypeError, ValueError):
            row["open_interest"] = None
    elif isinstance(oi, dict):
        # Store as numeric by extracting the value
        for key in ("openInterest", "open_interest", "oi", "value", "total"):
            if key in oi:
                try:
                    row["open_interest"] = float(oi[key])
                    break
                except (TypeError, ValueError):
                    pass
        else:
            row["open_interest"] = None
    elif oi is not None and not isinstance(oi, (int, float)):
        row["open_interest"] = None

    # collected_at as datetime
    if isinstance(row.get("collected_at"), str):
        row["collected_at"] = datetime.fromisoformat(
            row["collected_at"].replace("Z", "+00:00")
        )

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, row)
            row_id = cur.fetchone()["id"]

    import nats_bus
    nats_bus.publish(
        f"pm.snapshots.{snapshot['market_id']}",
        {k: v for k, v in snapshot.items()
         if k not in ("price_history", "top_holders", "recent_trades")},
    )
    return row_id


def get_latest_snapshots(limit: int = 100) -> list[dict]:
    """
    Return the most recent snapshot for each market.
    Used by strategy engines to read data.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT DISTINCT ON (s.market_id)
                    s.*,
                    m.question,
                    m.event_slug,
                    m.tags,
                    m.neg_risk
                FROM snapshots s
                JOIN markets m ON m.market_id = s.market_id
                ORDER BY s.market_id, s.collected_at DESC
                LIMIT %s
            """, (limit,))
            rows = []
            for r in cur.fetchall():
                row = dict(r)
                # Deserialise JSONB fields back to Python objects
                for field in ("price_history", "top_holders", "recent_trades"):
                    val = row.get(field)
                    if isinstance(val, str):
                        try:
                            row[field] = json.loads(val)
                        except Exception:
                            row[field] = []
                    elif val is None:
                        row[field] = []
                rows.append(row)
            return rows


def get_snapshots_for_market(market_id: str, limit: int = 336) -> list[dict]:
    """Return recent snapshots for one market (default 7 days at 30min intervals)."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT s.*, m.question, m.tags, m.neg_risk
                FROM snapshots s
                JOIN markets m ON m.market_id = s.market_id
                WHERE s.market_id = %s
                ORDER BY s.collected_at DESC
                LIMIT %s
            """, (market_id, limit))
            return [dict(r) for r in cur.fetchall()]


def prune_old_snapshots() -> int:
    """
    Delete snapshots older than SNAPSHOT_RETENTION_DAYS.
    Called once per UTC day from the main pipeline.
    Returns the number of rows deleted.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM snapshots WHERE collected_at < NOW() - (%s * INTERVAL '1 day')",
                (SNAPSHOT_RETENTION_DAYS,),
            )
            n = cur.rowcount
    if n:
        logger.info(f"Snapshot prune: removed {n} row(s) older than {SNAPSHOT_RETENTION_DAYS} days")
    return n


def get_neg_risk_snapshots_by_event() -> dict[str, list[dict]]:
    """
    Return latest snapshot per market grouped by event_slug,
    filtered to neg_risk markets only. Used by neg_risk_engine.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT DISTINCT ON (s.market_id)
                    s.market_id,
                    s.yes_price,
                    s.yes_ask,
                    s.collected_at,
                    m.question,
                    m.event_slug,
                    m.neg_risk
                FROM snapshots s
                JOIN markets m ON m.market_id = s.market_id
                WHERE m.neg_risk = TRUE
                ORDER BY s.market_id, s.collected_at DESC
            """)
            events: dict[str, list[dict]] = {}
            for row in cur.fetchall():
                slug = row["event_slug"] or "unknown"
                events.setdefault(slug, []).append(dict(row))
            return events


# ── signals table ─────────────────────────────────────────────────────────────

def insert_signal(signal: dict) -> int:
    """
    Insert one signal. Returns the new row id, or -1 if a duplicate exists
    for (strategy, market_id/event_slug) within the current clock-hour.

    Deduplication is enforced by the signals_dedup_hourly unique index, making
    this operation atomic — no TOCTOU race between a separate check and insert.
    """
    market_id  = signal.get("market_id")
    event_slug = signal.get("event_slug")
    strategy   = signal["strategy"]

    row = {
        "strategy":     strategy,
        "market_id":    market_id,
        "event_slug":   event_slug,
        "signal_score": signal.get("signal_score"),
        "metadata":     json.dumps(signal),
    }

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO signals (
                    strategy, market_id, event_slug, signal_score, metadata, emitted_at
                ) VALUES (
                    %(strategy)s, %(market_id)s, %(event_slug)s,
                    %(signal_score)s, %(metadata)s, NOW()
                )
                ON CONFLICT DO NOTHING
                RETURNING id
            """, row)
            result = cur.fetchone()

    if result is None:
        return -1   # duplicate within the clock-hour

    row_id = result["id"]

    from config import SIGNAL_WEBHOOK_URL
    if SIGNAL_WEBHOOK_URL:
        try:
            from agents.webhook_dispatcher import fire_signal
            fire_signal(strategy, market_id or "", dict(signal))
        except Exception as e:
            logger.warning(f"Webhook fire failed: {e}")

    import nats_bus
    nats_bus.publish(
        f"pm.signals.{strategy}.{market_id or 'unknown'}",
        dict(signal),
    )

    return row_id


def get_recent_signals(strategy: str = None, hours: int = 24, limit: int = 100) -> list[dict]:
    """Fetch recent signals, optionally filtered by strategy."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            if strategy:
                cur.execute("""
                    SELECT s.*, m.question
                    FROM signals s
                    LEFT JOIN markets m ON m.market_id = s.market_id
                    WHERE s.strategy = %s
                      AND s.emitted_at > NOW() - (%s * INTERVAL '1 hour')
                    ORDER BY s.emitted_at DESC
                    LIMIT %s
                """, (strategy, hours, limit))
            else:
                cur.execute("""
                    SELECT s.*, m.question
                    FROM signals s
                    LEFT JOIN markets m ON m.market_id = s.market_id
                    WHERE s.emitted_at > NOW() - (%s * INTERVAL '1 hour')
                    ORDER BY s.emitted_at DESC
                    LIMIT %s
                """, (hours, limit))
            return [dict(r) for r in cur.fetchall()]


def get_signal_counts() -> dict:
    """Return signal counts per strategy for the last 24h. Used by summary."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT strategy, COUNT(*) as count
                FROM signals
                WHERE emitted_at > NOW() - INTERVAL '24 hours'
                GROUP BY strategy
            """)
            return {r["strategy"]: r["count"] for r in cur.fetchall()}


def get_unresolved_signals(strategy: str = None, older_than_hours: int = 2) -> list[dict]:
    """
    Return unresolved signals older than the given window.
    Used by outcome_tracker to find signals ready for resolution.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            if strategy:
                cur.execute("""
                    SELECT s.*, m.tags
                    FROM signals s
                    LEFT JOIN markets m ON m.market_id = s.market_id
                    WHERE s.resolved = FALSE
                      AND s.strategy = %s
                      AND s.emitted_at < NOW() - (%s * INTERVAL '1 hour')
                    ORDER BY s.emitted_at DESC
                    LIMIT 500
                """, (strategy, older_than_hours))
            else:
                cur.execute("""
                    SELECT s.*, m.tags
                    FROM signals s
                    LEFT JOIN markets m ON m.market_id = s.market_id
                    WHERE s.resolved = FALSE
                      AND s.emitted_at < NOW() - (%s * INTERVAL '1 hour')
                    ORDER BY s.emitted_at DESC
                    LIMIT 500
                """, (older_than_hours,))
            rows = []
            for r in cur.fetchall():
                row = dict(r)
                if isinstance(row.get("metadata"), str):
                    try:
                        row["metadata"] = json.loads(row["metadata"])
                    except Exception:
                        row["metadata"] = {}
                rows.append(row)
            return rows


def update_signal_outcome(signal_id: int, exit_price: float, pnl: float, outcome: bool) -> None:
    """Mark a signal as resolved with its computed outcome and pnl."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE signals
                SET resolved   = TRUE,
                    exit_price = %s,
                    pnl        = %s,
                    outcome    = %s
                WHERE id = %s
            """, (exit_price, pnl, outcome, signal_id))


def get_snapshot_pairs(min_hours: float = 0.25, max_hours: float = 2.0) -> list[dict]:
    """
    Return (latest, reference) snapshot pair for each market.
    Used by odds_shift_engine to detect meaningful price movements.

    'reference' is the most recent snapshot that is between min_hours and max_hours old,
    so the comparison window is configurable and independent of collection frequency.
    With 30s collection cadence, rn=2 pairs are ~30s apart — far too short for a
    meaningful shift signal. This query deliberately looks back a real time window.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                WITH latest AS (
                    SELECT DISTINCT ON (market_id)
                        market_id, yes_price, collected_at, spread
                    FROM snapshots
                    WHERE yes_price IS NOT NULL
                    ORDER BY market_id, collected_at DESC
                ),
                reference AS (
                    SELECT DISTINCT ON (market_id)
                        market_id,
                        yes_price  AS ref_price,
                        collected_at AS ref_at
                    FROM snapshots
                    WHERE yes_price IS NOT NULL
                      AND collected_at <= NOW() - (%(min_h)s * INTERVAL '1 hour')
                      AND collected_at >= NOW() - (%(max_h)s * INTERVAL '1 hour')
                    ORDER BY market_id, collected_at DESC
                )
                SELECT
                    l.market_id,
                    l.yes_price    AS latest_price,
                    l.collected_at AS latest_at,
                    l.spread       AS latest_spread,
                    r.ref_price    AS prev_price,
                    r.ref_at       AS prev_at,
                    m.question, m.tags, m.event_slug, m.neg_risk, m.category
                FROM latest l
                JOIN reference r  ON l.market_id = r.market_id
                JOIN markets m    ON l.market_id = m.market_id
            """, {"min_h": min_hours, "max_h": max_hours})
            return [dict(r) for r in cur.fetchall()]


# ── orders table ─────────────────────────────────────────────────────────────

def insert_order(order: dict) -> int:
    """
    Insert a new order row. Returns the new row id.
    Raises IntegrityError if clord_id already exists (duplicate guard).
    """
    sql = """
        INSERT INTO orders (
            clord_id, signal_id, market_id, token_id, side,
            price, size_usdc, strategy, status,
            expiration_ts, reprice_of
        ) VALUES (
            %(clord_id)s, %(signal_id)s, %(market_id)s, %(token_id)s, %(side)s,
            %(price)s, %(size_usdc)s, %(strategy)s, 'PENDING_SUBMISSION',
            %(expiration_ts)s, %(reprice_of)s
        )
        RETURNING id
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, order)
            return cur.fetchone()["id"]


def update_order_status(
    clord_id: str,
    status: str,
    *,
    exchange_order_id: str = None,
    working_qty: float = None,
    filled_qty: float = None,
    fill_price: float = None,
    error_msg: str = None,
    submitted_at=None,
    filled_at=None,
    canceled_at=None,
) -> None:
    """Update order state. Only non-None kwargs are written."""
    fields = ["status = %(status)s"]
    params: dict = {"clord_id": clord_id, "status": status}

    if exchange_order_id is not None:
        fields.append("exchange_order_id = %(exchange_order_id)s")
        params["exchange_order_id"] = exchange_order_id
    if working_qty is not None:
        fields.append("working_qty = %(working_qty)s")
        params["working_qty"] = working_qty
    if filled_qty is not None:
        fields.append("filled_qty = %(filled_qty)s")
        params["filled_qty"] = filled_qty
    if fill_price is not None:
        fields.append("fill_price = %(fill_price)s")
        params["fill_price"] = fill_price
    if error_msg is not None:
        fields.append("error_msg = %(error_msg)s")
        params["error_msg"] = error_msg
    if submitted_at is not None:
        fields.append("submitted_at = %(submitted_at)s")
        params["submitted_at"] = submitted_at
    if filled_at is not None:
        fields.append("filled_at = %(filled_at)s")
        params["filled_at"] = filled_at
    if canceled_at is not None:
        fields.append("canceled_at = %(canceled_at)s")
        params["canceled_at"] = canceled_at

    sql = f"UPDATE orders SET {', '.join(fields)} WHERE clord_id = %(clord_id)s"
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)


def get_open_orders() -> list[dict]:
    """Return all orders in a non-terminal state (for fill polling)."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT * FROM orders
                WHERE status IN ('PENDING_SUBMISSION', 'SENT', 'OPEN', 'PARTIALLY_FILLED')
                ORDER BY created_at ASC
            """)
            return [dict(r) for r in cur.fetchall()]


def order_exists_for_signal(signal_id: int) -> bool:
    """Return True if any order (any status) was already created for this signal."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM orders WHERE signal_id = %s LIMIT 1",
                (signal_id,),
            )
            return cur.fetchone() is not None


def mark_signal_executed(signal_id: int) -> None:
    """Mark a signal as handed off to the executor."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE signals SET executed = TRUE WHERE id = %s",
                (signal_id,),
            )


def get_executable_signals(
    min_score: float,
    strategies: list[str],
    max_age_secs: int,
    limit: int = 20,
) -> list[dict]:
    """
    Return unexecuted signals that are fresh, high-score, and in an allowed strategy.
    Ordered by signal_score DESC so the best opportunities are processed first.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT s.*, m.token_ids, m.outcomes
                FROM signals s
                LEFT JOIN markets m ON m.market_id = s.market_id
                WHERE s.executed = FALSE
                  AND s.signal_score >= %(min_score)s
                  AND s.strategy = ANY(%(strategies)s)
                  AND s.emitted_at > NOW() - (%(max_age_secs)s * INTERVAL '1 second')
                ORDER BY s.signal_score DESC
                LIMIT %(limit)s
            """, {
                "min_score": min_score,
                "strategies": strategies,
                "max_age_secs": max_age_secs,
                "limit": limit,
            })
            rows = []
            for r in cur.fetchall():
                row = dict(r)
                if isinstance(row.get("metadata"), str):
                    try:
                        row["metadata"] = json.loads(row["metadata"])
                    except Exception:
                        row["metadata"] = {}
                rows.append(row)
            return rows


# ── positions table ───────────────────────────────────────────────────────────

def upsert_position(
    market_id: str,
    token_id: str,
    side: str,
    *,
    delta_bought: float = 0,
    delta_sold: float = 0,
    delta_working_buy: float = 0,
    delta_working_sell: float = 0,
    avg_cost: float = None,
    pnl_realized_delta: float = 0,
) -> None:
    """
    Create or update a position row by applying deltas.
    All quantity changes are additive so concurrent updates don't race.
    avg_cost is only written when explicitly provided (on fill).
    """
    params: dict = {
        "market_id": market_id,
        "token_id": token_id,
        "side": side,
        "delta_bought": delta_bought,
        "delta_sold": delta_sold,
        "delta_working_buy": delta_working_buy,
        "delta_working_sell": delta_working_sell,
        "pnl_realized_delta": pnl_realized_delta,
        "avg_cost": avg_cost,
    }
    avg_cost_clause = (
        "avg_cost = %(avg_cost)s,"
        if avg_cost is not None
        else ""
    )
    sql = f"""
        INSERT INTO positions (market_id, token_id, side, total_bought, total_sold,
            working_buy, working_sell, avg_cost, pnl_realized, last_updated)
        VALUES (%(market_id)s, %(token_id)s, %(side)s,
            %(delta_bought)s, %(delta_sold)s,
            %(delta_working_buy)s, %(delta_working_sell)s,
            %(avg_cost)s, %(pnl_realized_delta)s, NOW())
        ON CONFLICT (market_id, token_id, side) DO UPDATE SET
            total_bought  = positions.total_bought  + %(delta_bought)s,
            total_sold    = positions.total_sold    + %(delta_sold)s,
            working_buy   = positions.working_buy   + %(delta_working_buy)s,
            working_sell  = positions.working_sell  + %(delta_working_sell)s,
            {avg_cost_clause}
            pnl_realized  = positions.pnl_realized  + %(pnl_realized_delta)s,
            last_updated  = NOW()
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)


def get_position(market_id: str, token_id: str, side: str) -> dict | None:
    """Return a single position row or None if no position exists."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT * FROM positions
                WHERE market_id = %s AND token_id = %s AND side = %s
            """, (market_id, token_id, side))
            row = cur.fetchone()
            return dict(row) if row else None


def get_total_open_exposure() -> float:
    """
    Sum of size_usdc across all non-terminal and filled-but-held orders.
    Used by the pre-trade gate to enforce the portfolio exposure cap.
    The 8-day window covers the max market lifetime (168h) plus buffer.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT COALESCE(SUM(size_usdc), 0) AS total
                FROM orders
                WHERE status IN (
                    'PENDING_SUBMISSION', 'SENT', 'OPEN',
                    'PARTIALLY_FILLED', 'FILLED'
                )
                AND created_at > NOW() - INTERVAL '8 days'
            """)
            return float(cur.fetchone()["total"])


def get_expired_unfilled_orders() -> list[dict]:
    """
    Return CANCELED orders with no fills that were GTD-expired (not user-canceled)
    and have not yet been repriced. Joined with markets for hours_to_close.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT o.*, m.token_ids, m.hours_to_close, m.question, m.tags
                FROM orders o
                LEFT JOIN markets m ON m.market_id = o.market_id
                WHERE o.status = 'CANCELED'
                  AND (o.filled_qty IS NULL OR o.filled_qty = 0)
                  AND o.repriced = FALSE
                  AND o.expiration_ts IS NOT NULL
                  AND o.expiration_ts <= NOW()
            """)
            return [dict(r) for r in cur.fetchall()]


def mark_order_repriced(order_id: int) -> None:
    """Mark an order as repriced so it is not picked up again next cycle."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE orders SET repriced = TRUE WHERE id = %s",
                (order_id,),
            )


def get_snapshot_for_reprice(market_id: str) -> dict | None:
    """
    Latest snapshot for a single market, including hours_to_close from markets.
    Used by the executor's reprice evaluator.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT DISTINCT ON (s.market_id)
                    s.*,
                    m.question, m.event_slug, m.tags,
                    m.neg_risk, m.hours_to_close
                FROM snapshots s
                JOIN markets m ON m.market_id = s.market_id
                WHERE s.market_id = %s
                ORDER BY s.market_id, s.collected_at DESC
            """, (market_id,))
            row = cur.fetchone()
            return dict(row) if row else None


def get_all_open_positions() -> list[dict]:
    """Return all positions with non-zero net exposure or working qty."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT * FROM positions
                WHERE (total_bought - total_sold) != 0
                   OR working_buy != 0
                   OR working_sell != 0
                ORDER BY last_updated DESC
            """)
            return [dict(r) for r in cur.fetchall()]


def get_db_stats() -> dict:
    """Quick stats for the health endpoint and run summary."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) as n FROM markets")
            markets = cur.fetchone()["n"]
            cur.execute("SELECT COUNT(*) as n FROM snapshots")
            snapshots = cur.fetchone()["n"]
            cur.execute("SELECT COUNT(*) as n FROM signals")
            signals = cur.fetchone()["n"]
            cur.execute("""
                SELECT MAX(collected_at) as last
                FROM snapshots
            """)
            last_snapshot = cur.fetchone()["last"]
    return {
        "markets":       markets,
        "snapshots":     snapshots,
        "signals":       signals,
        "last_snapshot": last_snapshot.isoformat() if last_snapshot else None,
    }
