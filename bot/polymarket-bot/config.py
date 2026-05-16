# config.py — single source of truth for all constants
import os

# ── API base URLs ──────────────────────────────────────────────────────────────
GAMMA_API  = "https://gamma-api.polymarket.com"
CLOB_API   = "https://clob.polymarket.com"
DATA_API   = "https://data-api.polymarket.com"

# ── Scheduler ─────────────────────────────────────────────────────────────────
POLL_INTERVAL_SECONDS = 30   # seconds between pipeline runs (≈ 3 min per scan cycle at SCAN_INTERVAL_RUNS=6)

# ── Market scanner filters ────────────────────────────────────────────────────
SCANNER_LIMIT           = 100   # markets fetched per page from Gamma
SCANNER_PAGES           = 5     # pages to scan (= up to 500 markets); increased to offset tighter time filter
MIN_VOLUME_24H          = 5_000  # USD — ignore micro-markets
MIN_LIQUIDITY           = 2_000  # USD — need enough depth to matter
MIN_TOP_BOOK_DEPTH      = 50    # USD — minimum size available at the best bid/ask to consider the spread/arb valid
MIN_HOURS_TO_CLOSE      = 1      # Allow scanning right up to the final hours
MAX_HOURS_TO_CLOSE      = 168   # skip markets expiring beyond 7 days — laser focus on short-resolution markets
MAX_WATCHLIST_SIZE      = 50    # broader opportunity surface across strategies

# Price range filter for strategy relevance:
# - Spread engine:    best between 0.20–0.80 (fees are meaningful)
# - Neg-risk engine:  any price (over-round can occur anywhere)
# - Reversion engine: best between 0.10–0.90 (need room to move)
PRICE_MIN = 0.05
PRICE_MAX = 0.95

# ── Data collector ────────────────────────────────────────────────────────────
PRICE_HISTORY_FIDELITY  = "1h"   # interval for price history (1m, 5m, 1h, 1d)
PRICE_HISTORY_LIMIT     = 168    # data points to fetch (168 × 1h = 7 days)
SNAPSHOT_RETENTION_DAYS = 30     # snapshots older than this are eligible for cleanup
# Deep collection (price_history, open_interest, top_holders, recent_trades) runs every
# Nth pipeline tick. Light fields (midpoint, spread, yes/no ask, fee) run every tick.
DEEP_COLLECTION_INTERVAL = 6     # 1 deep run per 6 ticks ≈ every 3 min at 30s poll

# ── Spread engine thresholds ──────────────────────────────────────────────────
# Fee formula: fee = C × p × feeRate × (p × (1-p))^exponent
# Peak effective rates by category (post March-30 structure):
#   Geopolitics: 0%   Sports: 0.75%   Politics: 1.0%   Crypto: 1.80%
# Flag a market when spread > SPREAD_FEE_MULTIPLE × estimated_fee
SPREAD_FEE_MULTIPLE     = 2.0    # spread must be at least 2× the fee to be interesting
SPREAD_MIN_SIGNAL_SCORE = 0.6    # 0–1 score threshold to include in report
SPREAD_MIN_YES_PRICE    = 0.05   # reject markets effectively resolved NO (< 5¢)
SPREAD_MAX_YES_PRICE    = 0.95   # reject markets effectively resolved YES (> 95¢)

# ── Neg-risk engine thresholds ────────────────────────────────────────────────
# TAKER: Buy all outcomes instantly. Sum of ASKS must be < 1.0 (after fees).
NEG_RISK_TAKER_THRESHOLD = 0.98      # flag when sum(asks) < 0.98
# MAKER: Sell all outcomes. Sum of BIDS must be > 1.0 (after fees).
NEG_RISK_MAKER_THRESHOLD = 1.02      # flag when sum(bids) > 1.02
NEG_RISK_MIN_OUTCOMES    = 3         # only interesting with 3+ outcomes

# ── Fee rate lookup (post March-30 structure) ─────────────────────────────────
# Used by spread engine to estimate fee cost. Maps category tag → (rate, exponent)
FEE_RATES = {
    "crypto":      (0.072, 1),
    "sports":      (0.03,  1),
    "finance":     (0.04,  1),
    "politics":    (0.04,  1),
    "economics":   (0.03,  0.5),
    "culture":     (0.05,  1),
    "weather":     (0.025, 0.5),
    "tech":        (0.04,  1),
    "geopolitics": (0.0,   1),   # fee-free
    "other":       (0.2,   2),
}
DEFAULT_FEE_RATE = (0.04, 1)  # fallback if category not matched

# ── EV calculator ─────────────────────────────────────────────────────────────
EV_MIN_THRESHOLD        = 0.03   # minimum EV (3%) to include annotation
KELLY_FRACTION          = 0.25   # quarter Kelly — safer, less ruin risk

# ── Market scoring — volume sweetspot ─────────────────────────────────────────
# Score peaks at VOLUME_SWEET_SPOT_PEAK and falls off above VOLUME_SWEET_SPOT_MAX.
# Mega-whale markets (billions) are deprioritised — harder to find edge.
VOLUME_SWEET_SPOT_PEAK  = 100_000    # USD — peak score volume
VOLUME_SWEET_SPOT_MAX   = 2_000_000  # USD — above this, score starts falling

# ── Micro-event category tagger ───────────────────────────────────────────────
# Maps category name → list of keywords to match against market question (lowercase).
# First matching category wins. "other" is the fallback.
MICRO_EVENT_KEYWORDS: dict[str, list[str]] = {
    "election_sub":  ["who will", "vp pick", "vice president", "running mate", "nominee", "primary"],
    "ceasefire":     ["ceasefire", "peace deal", "truce", "end the war", "hostage"],
    "scandal":       ["resign", "arrested", "charged", "impeach", "scandal", "indicted", "fired"],
    "crypto_event":  ["bitcoin", " eth ", "ethereum", "crypto", "halving", "etf", "sec approve"],
    "sports":        ["nba", "nfl", "nhl", "mlb", "fifa", "champion", "super bowl", "world cup", "playoffs"],
    "corporate":     ["merger", "acquisition", "ipo", "earnings", "bankrupt", "ceo"],
    "legal":         ["verdict", "ruling", "supreme court", "lawsuit", "trial", "conviction"],
    "geopolitical":  ["war", "invasion", "sanction", "nato", "missile", "attack", "troops"],
}

# ── Tail-yield engine ─────────────────────────────────────────────────────────
YIELD_MIN_PRICE         = 0.95   # minimum YES price eligible for yield harvest
YIELD_HOURS_TO_EXPIRY   = 48     # skip markets expiring beyond this many hours

# ── Execution layer — risk and strategy parameters ────────────────────────────
# BANKROLL_USDC is read from the BANKROLL_USDC env var (set in Railway service variables).
# Defaults to 0.0 (execution blocked) so a missing var never causes over-exposure.
BANKROLL_USDC         = float(os.environ.get("BANKROLL_USDC", "0.0"))
MAX_POSITION_PCT      = 0.10   # max fraction of bankroll per single position
MAX_PORTFOLIO_PCT     = 0.33   # max fraction of bankroll open across all positions
MAX_SIGNAL_AGE_SECS   = 60     # reject signals older than this (seconds)
ORDER_MAX_RETRIES     = 5      # exponential backoff attempts before REJECTED
EXECUTOR_POLL_SECS    = 10     # fallback poll interval (NATS fast-path is faster)
# EXECUTION_STRATEGIES is read from the env var of the same name (Railway service variable).
# Set it to a comma-separated list to restrict which engines place live orders.
# Unknown strategy names are silently dropped; an empty result means no orders execute.
# Defaults to all three engines when the var is unset.
# Examples:
#   EXECUTION_STRATEGIES=tail_yield_engine
#   EXECUTION_STRATEGIES=spread_engine,tail_yield_engine
_KNOWN_STRATEGIES = {"spread_engine", "tail_yield_engine", "neg_risk_overround"}
_DEFAULT_STRATEGIES = ["spread_engine", "tail_yield_engine", "neg_risk_overround"]
_raw_strategies = os.environ.get("EXECUTION_STRATEGIES", "").strip()
if _raw_strategies:
    EXECUTION_STRATEGIES = [
        s.strip() for s in _raw_strategies.split(",")
        if s.strip() in _KNOWN_STRATEGIES
    ]
else:
    EXECUTION_STRATEGIES = _DEFAULT_STRATEGIES

EXECUTION_MIN_SCORE   = float(os.environ.get("EXECUTION_MIN_SCORE", "0.75"))

# GTD order lifetimes (seconds). Polymarket enforces a 60s minimum buffer on top.
ORDER_TTL_SPREAD_SECS   = 600    # spread_engine: 10 min — stale quote = no edge
ORDER_TTL_NEG_RISK_SECS = 120    # neg_risk_overround: 2 min — arb closes fast, partial fills are risky
ORDER_TTL_TAIL_SECS     = 3600   # tail_yield_engine: 60 min — near-certain prices move slowly
ORDER_TTL_EXIT_SECS     = 3600   # exit orders: 60 min — if unfilled in an hour, market is illiquid

# ── Exit manager — dynamic exit thresholds ───────────────────────────────────
# tail_yield: exit when annualised hold-yield drops below this floor.
#   hold_yield = (1 - price) / price × (8760 / hours_to_expiry)
#   At 0.98 with 48h left ≈ 3.7% — below threshold → exit.
#   At 0.97 with 1h left  ≈ 277%  — above threshold → hold.
TAIL_YIELD_MIN_HOLD_YIELD = 0.10   # 10% annualised floor

# spread_engine: exit when live spread compresses to ≤ this multiple of the
# estimated round-trip fee. 1.5× means exit while a thin edge still exists.
SPREAD_EXIT_FEE_MULTIPLE  = 1.5

# Trailing stop — ratchets up as price rises; triggers if price retreats
# more than TRAIL_PIPS below the running peak since position was opened.
# Only activates after position has shown any profit (peak > avg_cost).
TRAIL_PIPS_TAIL           = 0.005  # 0.5¢ for tail_yield (high-conviction, tight)
TRAIL_PIPS_SPREAD         = 0.01   # 1¢   for spread_engine

# ── Infrastructure — set in Railway service variables, never in code ──────────
# BANKROLL_USDC        — USDC allocated to execution  (read above in this file)
# POLYGON_PRIVATE_KEY  — L1 wallet key               (read in execution/auth.py)
# NATS_URL             — NATS server endpoint         (read in nats_bus.py)
# SIGNAL_WEBHOOK_URL   — external HTTP webhook        (read in webhook_dispatcher.py)
# POLYMARKET_CHAIN_ID  — 137 mainnet / 80002 testnet  (read in execution/auth.py)
SIGNAL_WEBHOOK_URL      = os.environ.get("SIGNAL_WEBHOOK_URL", "")
NATS_URL                = os.environ.get("NATS_URL", "")

# ── Logging ───────────────────────────────────────────────────────────────────
LOG_RETENTION_DAYS      = 14   # delete log files older than this many days
ZERO_SIGNAL_STREAK_WARN = 6    # warn after N consecutive zero-signal runs (~3 min at 30s interval)

# ── Paths ─────────────────────────────────────────────────────────────────────
WATCHLIST_PATH   = "data/watchlist/watched_markets.json"
SNAPSHOTS_DIR    = "data/snapshots"
LOGS_DIR         = "logs"
REPORTS_DIR      = "reports"
STATE_DIR        = "state"   # lightweight runtime state (streak counters, etc.)
