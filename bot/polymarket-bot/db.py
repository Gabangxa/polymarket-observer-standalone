# db.py — Postgres connection, schema, and query helpers
#
# On Replit: DATABASE_URL is injected automatically once you provision
# the database from Tools → Database in the workspace.
#
# Schema:
#   markets    — one row per watched market (upserted each scan)
#   snapshots  — one row per market per collection run
#   signals    — one row per signal emitted by any strategy engine

import json
import logging
import os
from contextlib import contextmanager
from datetime import datetime, timezone

import psycopg2
import psycopg2.extras

logger = logging.getLogger(__name__)

# ── Connection ────────────────────────────────────────────────────────────────

def _get_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL environment variable not set. "
            "Provision the database from Tools → Database in your Replit workspace."
        )
    return url


@contextmanager
def get_conn():
    """Context manager yielding a psycopg2 connection. Auto-commits on exit."""
    conn = psycopg2.connect(_get_url(), cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


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
    errors          TEXT[]
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

-- Migration: add outcome column to existing tables
ALTER TABLE signals ADD COLUMN IF NOT EXISTS outcome BOOLEAN;
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
            tags, neg_risk, token_ids, outcomes,
            volume_24h, liquidity, end_date, hours_to_close,
            fees_enabled, score, added_at, updated_at
        ) VALUES (
            %(market_id)s, %(condition_id)s, %(question)s, %(event_title)s, %(event_slug)s,
            %(tags)s, %(neg_risk)s, %(token_ids)s, %(outcomes)s,
            %(volume_24h)s, %(liquidity)s, %(end_date)s, %(hours_to_close)s,
            %(fees_enabled)s, %(score)s, NOW(), NOW()
        )
        ON CONFLICT (market_id) DO UPDATE SET
            question        = EXCLUDED.question,
            event_title     = EXCLUDED.event_title,
            tags            = EXCLUDED.tags,
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
    """Return all markets currently in the watchlist, ordered by score."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT * FROM markets
                ORDER BY score DESC NULLS LAST
            """)
            return [dict(r) for r in cur.fetchall()]


# ── snapshots table ───────────────────────────────────────────────────────────

def insert_snapshot(snapshot: dict) -> int:
    """Insert one snapshot row. Returns the new row id."""
    sql = """
        INSERT INTO snapshots (
            market_id, collected_at,
            yes_price, no_price, spread, midpoint, fee_rate_bps,
            open_interest, price_history, top_holders, recent_trades, errors
        ) VALUES (
            %(market_id)s, %(collected_at)s,
            %(yes_price)s, %(no_price)s, %(spread)s, %(midpoint)s, %(fee_rate_bps)s,
            %(open_interest)s, %(price_history)s, %(top_holders)s, %(recent_trades)s, %(errors)s
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
            return cur.fetchone()["id"]


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
    Insert one signal. Deduplicates within the same hour by
    (strategy, market_id/event_slug). Returns new row id or -1 if duplicate.
    """
    market_id  = signal.get("market_id")
    event_slug = signal.get("event_slug")
    strategy   = signal["strategy"]

    # Deduplicate: skip if same signal already emitted in the last hour
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id FROM signals
                WHERE strategy = %s
                  AND (market_id = %s OR event_slug = %s)
                  AND emitted_at > NOW() - INTERVAL '1 hour'
                LIMIT 1
            """, (strategy, market_id, event_slug))
            if cur.fetchone():
                return -1   # duplicate within the hour

    sql = """
        INSERT INTO signals (
            strategy, market_id, event_slug, signal_score, metadata, emitted_at
        ) VALUES (
            %(strategy)s, %(market_id)s, %(event_slug)s,
            %(signal_score)s, %(metadata)s, NOW()
        )
        RETURNING id
    """
    row = {
        "strategy":     strategy,
        "market_id":    market_id,
        "event_slug":   event_slug,
        "signal_score": signal.get("signal_score"),
        "metadata":     json.dumps(signal),
    }
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, row)
            return cur.fetchone()["id"]


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
                      AND s.emitted_at > NOW() - INTERVAL '%s hours'
                    ORDER BY s.emitted_at DESC
                    LIMIT %s
                """, (strategy, hours, limit))
            else:
                cur.execute("""
                    SELECT s.*, m.question
                    FROM signals s
                    LEFT JOIN markets m ON m.market_id = s.market_id
                    WHERE s.emitted_at > NOW() - INTERVAL '%s hours'
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
                      AND s.emitted_at < NOW() - INTERVAL '%s hours'
                    ORDER BY s.emitted_at DESC
                    LIMIT 500
                """, (strategy, older_than_hours))
            else:
                cur.execute("""
                    SELECT s.*, m.tags
                    FROM signals s
                    LEFT JOIN markets m ON m.market_id = s.market_id
                    WHERE s.resolved = FALSE
                      AND s.emitted_at < NOW() - INTERVAL '%s hours'
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
