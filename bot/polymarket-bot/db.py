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
    Delete markets (and their snapshots) whose market_id is not in keep_ids.
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
            f"Watchlist pruned: removed {market_count} market(s) "
            f"and {snap_count} orphaned snapshot(s)"
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
