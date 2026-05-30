# polymarket-bot

Observer + executor for Polymarket prediction markets. Continuously scans markets, snapshots price data, runs strategy engines that emit signals to Postgres, and executes qualifying signals as live CLOB orders.

For protocol-level execution flow, state machine, and config reference, see
[`execution/EXECUTION_LOGIC.md`](execution/EXECUTION_LOGIC.md). For agent
constitution and hard constraints, see [`CLAUDE.md`](CLAUDE.md).

## Stack

- **Python 3.11+** — single process, multiple daemon threads
- **httpx** — public Polymarket APIs (Gamma, CLOB read, Data)
- **py-clob-client-v2** — authenticated CLOB writes
- **PostgreSQL** — source of truth for signals, orders, positions
- **Flask** — HTTP server for health, kill switches, dashboard reads
- **NATS** (optional) — fast-path signal wake + telemetry pub/sub

## Project structure

```
polymarket-bot/
├── CLAUDE.md                       # Agent constitution (read first)
├── scheduler.py                    # Entry point — Flask + pipeline + executor threads
├── main.py                         # One pipeline iteration
├── server.py                       # Flask: /health, /signals, /watchlist, /execution/*
├── api.py                          # Public Polymarket API wrappers
├── db.py                           # Postgres pool, schema, all helpers
├── config.py                       # Single source of truth for tunables
├── nats_bus.py                     # NATS pub/sub bridge (telemetry only)
├── alerts.py                       # Discord / webhook notifications
├── send_order.py                   # Standalone order-send utility (dev)
│
├── agents/                         # Strategy engines — write to `signals` table
│   ├── market_scanner.py           # Score + select watchlist from Gamma API
│   ├── data_collector.py           # Snapshot each watched market into Postgres
│   ├── pnl_tracker.py              # Mark open positions to current price
│   ├── spread_engine.py            # Spread > N× round-trip fee
│   ├── neg_risk_engine.py          # Over-round across multi-outcome events
│   ├── tail_yield_engine.py        # Near-certain YES (≥ 95¢) with positive hold-yield
│   ├── outcome_tracker.py          # Resolve short-window signals
│   └── hindsight_logger.py         # Resolve directional signals on market resolution
│
├── execution/                      # Live CLOB execution layer
│   ├── EXECUTION_LOGIC.md          # Full execution documentation
│   ├── auth.py                     # py-clob-client-v2 singleton; L2 derived from PK
│   ├── executor.py                 # Daemon loop: signals → orders → fills
│   ├── pre_trade_gate.py           # 6 gates before any CLOB call
│   ├── order_manager.py            # Place / poll / cancel; Decimal arithmetic; backoff
│   ├── exit_manager.py             # NATS snapshot-driven dynamic exits
│   ├── reconciler.py               # Periodic DB ↔ CLOB drift detection
│   └── connection_checker.py       # CLOB reachable + wallet authenticated probes
│
├── tests/                          # pytest — engines, execution layer, order_manager
├── state/                          # Runtime state (rate limits, etc.)
└── logs/                           # run_YYYY-MM-DD.log (auto-created)
```

## Pipeline cadence

Each iteration of `scheduler.py`'s outer loop runs every `POLL_INTERVAL_SECONDS`
(default **30 s**). Every 12th iteration also refreshes the watchlist via the
market scanner (≈ every 6 minutes).

```
market_scanner → data_collector → pnl_tracker → spread_engine
              → neg_risk_engine → tail_yield_engine
              → outcome_tracker → hindsight_logger
```

The **executor** is a separate daemon thread that wakes either on a NATS
`pm.signals.>` event (fast path) or every `EXECUTOR_POLL_SECS` (default 10 s)
as a fallback.

## Strategies live in execution

| Strategy             | Direction                                 | Edge                                       |
|----------------------|-------------------------------------------|--------------------------------------------|
| `spread_engine`      | LIMIT BUY YES at `yes_ask − 1 tick`       | Structural — passive maker spread capture  |
| `tail_yield_engine`  | LIMIT BUY YES near 1.0                    | Statistical — time-value decay             |
| `neg_risk_overround` | Multi-leg BUY YES (taker) or SELL YES (maker) across outcomes | Structural — over-round arb |

Other engines (`binary_arb`, `odds_shift`, `reversion`) emit signals for
observation but are NOT in the live execution allowlist — they have unhedgeable
leg risk or need a fair-value model before execution is safe.

## HTTP endpoints

| Endpoint                       | Auth          | Purpose                                                  |
|--------------------------------|---------------|----------------------------------------------------------|
| `GET /`                        | none          | Liveness probe                                           |
| `GET /health`                  | none          | JSON: status + DB row counts + last snapshot time        |
| `GET /signals`                 | none          | Last 24 h signals + per-strategy counts                  |
| `GET /watchlist`               | none          | Current watched markets                                  |
| `GET /logs?lines=N&level=...`  | `X-API-Key`   | Tail today's log file                                    |
| `GET /execution/status`        | none          | Paused flag + open order count                           |
| `POST /execution/pause`        | `X-API-Key`   | Halt new signal processing (fill polling continues)      |
| `POST /execution/resume`       | `X-API-Key`   | Resume signal processing                                 |
| `POST /execution/cancel-all`   | `X-API-Key`   | Cancel every open CLOB order                             |

`X-API-Key` checks against `BOT_API_KEY`; kill switches are **fail-closed** if
`BOT_API_KEY` is unset.

## Running locally

```bash
cd bot/polymarket-bot
pip install -r ../../requirements.txt

# Required for the pipeline to run
export DATABASE_URL=postgresql://user:pass@host:5432/dbname

# Required to start the executor thread (otherwise it's a no-op)
export POLYGON_PRIVATE_KEY=0xabc...
export POLYMARKET_SIGNATURE_TYPE=2      # no default — must be set explicitly
export POLYMARKET_FUNDER=0xdef...       # NOT your EOA — the proxy/safe/deposit address

python scheduler.py
```

Schema is created automatically on first start via `db.init_schema()`.

## Configuration

Most tunables live in `config.py`. Key constants for the execution layer:

| Constant                       | Default                  | Purpose                                              |
|--------------------------------|--------------------------|------------------------------------------------------|
| `POLL_INTERVAL_SECONDS`        | `30`                     | Pipeline loop interval                               |
| `EXECUTOR_POLL_SECS`           | `10`                     | Executor fallback poll (NATS wake is faster)         |
| `EXECUTION_MIN_SCORE`          | `0.75`                   | Min `signal_score` to execute                        |
| `MAX_SIGNAL_AGE_SECS`          | `60`                     | Reject signals older than this                       |
| `MAX_POSITION_PCT`             | `0.10`                   | Per-position cap (10% of bankroll)                   |
| `MAX_PORTFOLIO_PCT`            | `0.33`                   | Total open exposure cap (33% of bankroll)            |
| `ORDER_TTL_SPREAD_SECS`        | `600`                    | GTD window for `spread_engine` (10 min)              |
| `ORDER_TTL_TAIL_SECS`          | `3600`                   | GTD window for `tail_yield_engine` (60 min)          |
| `ORDER_TTL_EXIT_SECS`          | `3600`                   | GTD window for exit SELL orders                      |
| `ORDER_MAX_RETRIES`            | `5`                      | Backoff attempts before REJECTED                     |
| `TAIL_YIELD_MIN_HOLD_YIELD`    | `0.10`                   | Yield-decay exit threshold (10% annualised)          |
| `SPREAD_EXIT_FEE_MULTIPLE`     | `1.5`                    | Spread-compression exit threshold                    |
| `TRAIL_PIPS_TAIL`              | `0.005`                  | Trailing-stop pips for `tail_yield_engine` (0.5¢)    |
| `TRAIL_PIPS_SPREAD`            | `0.01`                   | Trailing-stop pips for `spread_engine` (1¢)          |
| `EXIT_MAX_GIVEBACK_PCT`        | `0.05`                   | Refuse exit if `price < avg_cost × (1 − pct)`        |

Env-only:

| Variable                       | Default                  | Purpose                                              |
|--------------------------------|--------------------------|------------------------------------------------------|
| `POLYMARKET_SIGNATURE_TYPE`    | **none — raises**        | 0/1/2/3, must be set explicitly                      |
| `RECONCILER_ZOMBIE_CUTOFF_MINS`| `30`                     | Stuck-order cutoff (re-checked once before REJECT)   |
| `POLYMARKET_DEBUG_SIGN`        | unset                    | If `1`, dump signed EIP-712 payload to logs          |
| `NATS_URL`                     | unset                    | No-op when unset (timed-poll only)                   |

See [`execution/EXECUTION_LOGIC.md`](execution/EXECUTION_LOGIC.md) for the full
table including order state machine, neg-risk atomicity model, and
reconciliation buckets.

## Testing

```bash
cd bot/polymarket-bot
python -m pytest tests/
```

`tests/test_order_manager.py` covers the critical paths without Postgres
or live CLOB — DB and client are mocked. `tests/test_execution_layer.py`
and `tests/test_engines.py` exercise additional integration surface.

## Deployment

Deployed on Railway via `bot/polymarket-bot/Dockerfile`. See the root
[`README.md`](../../README.md) for the full monorepo deploy matrix.
