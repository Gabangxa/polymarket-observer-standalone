# config.py — single source of truth for all constants
import os

# ── API base URLs ──────────────────────────────────────────────────────────────
GAMMA_API  = "https://gamma-api.polymarket.com"
CLOB_API   = "https://clob.polymarket.com"
DATA_API   = "https://data-api.polymarket.com"

# ── Scheduler ─────────────────────────────────────────────────────────────────
POLL_INTERVAL_SECONDS = 300   # 5 minutes between full pipeline runs

# ── Market scanner filters ────────────────────────────────────────────────────
SCANNER_LIMIT           = 100   # markets fetched per page from Gamma
SCANNER_PAGES           = 5     # pages to scan (= up to 500 markets); increased to offset tighter time filter
MIN_VOLUME_24H          = 5_000  # USD — ignore micro-markets
MIN_LIQUIDITY           = 2_000  # USD — need enough depth to matter
MIN_HOURS_TO_CLOSE      = 24    # skip markets expiring within 24h
MAX_HOURS_TO_CLOSE      = 168   # skip markets expiring beyond 7 days — laser focus on short-resolution markets
MAX_WATCHLIST_SIZE      = 20    # keep the watchlist focused

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

# ── Spread engine thresholds ──────────────────────────────────────────────────
# Fee formula: fee = C × p × feeRate × (p × (1-p))^exponent
# Peak effective rates by category (post March-30 structure):
#   Geopolitics: 0%   Sports: 0.75%   Politics: 1.0%   Crypto: 1.80%
# Flag a market when spread > SPREAD_FEE_MULTIPLE × estimated_fee
SPREAD_FEE_MULTIPLE     = 2.0    # spread must be at least 2× the fee to be interesting
SPREAD_MIN_SIGNAL_SCORE = 0.6    # 0–1 score threshold to include in report

# ── Neg-risk engine thresholds ────────────────────────────────────────────────
# In a neg-risk event, sum(all outcome prices) should be ≤ 1.0
# When it exceeds this, there's a theoretical risk-free trade
NEG_RISK_OVERROUND_THRESHOLD = 1.02   # flag when sum > 1.02 (2¢ of slack)
NEG_RISK_MIN_OUTCOMES        = 3      # only interesting with 3+ outcomes

# ── Reversion engine thresholds ──────────────────────────────────────────────
REVERSION_PRICE_MOVE_THRESHOLD = 0.08   # 8 cent move in a short window
REVERSION_WINDOW_HOURS         = 2      # look back this many hours for the move
REVERSION_MAX_OI               = 50_000 # USD — only flag thin markets

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

# ── Odds shift engine ─────────────────────────────────────────────────────────
ODDS_SHIFT_THRESHOLD    = 0.05   # minimum price delta (5¢) to emit a signal
ODDS_SHIFT_MIN_HOURS    = 0.25   # minimum elapsed hours between snapshots to compute velocity

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

# ── Micro-spread engine ───────────────────────────────────────────────────────
MICRO_SPREAD_THRESHOLD  = 0.04   # minimum spread to emit a micro-spread signal

# ── Tail-yield engine ─────────────────────────────────────────────────────────
YIELD_MIN_PRICE         = 0.95   # minimum YES price eligible for yield harvest
YIELD_HOURS_TO_EXPIRY   = 48     # skip markets expiring beyond this many hours

# ── Binary arb engine ─────────────────────────────────────────────────────────
ARB_THRESHOLD           = 0.98   # fire when yes_ask + no_ask < this value

# ── Webhook dispatcher ────────────────────────────────────────────────────────
# Set SIGNAL_WEBHOOK_URL in Railway UI (bot service variables) — never hardcode
SIGNAL_WEBHOOK_URL      = os.environ.get("SIGNAL_WEBHOOK_URL", "")

# ── Logging ───────────────────────────────────────────────────────────────────
LOG_RETENTION_DAYS      = 14   # delete log files older than this many days
ZERO_SIGNAL_STREAK_WARN = 6    # warn after N consecutive zero-signal runs (~30 min at 5 min interval)

# ── Paths ─────────────────────────────────────────────────────────────────────
WATCHLIST_PATH   = "data/watchlist/watched_markets.json"
SNAPSHOTS_DIR    = "data/snapshots"
LOGS_DIR         = "logs"
REPORTS_DIR      = "reports"
STATE_DIR        = "state"   # lightweight runtime state (streak counters, etc.)
