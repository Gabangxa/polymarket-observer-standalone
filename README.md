# polymarket-observer-standalone

Monorepo for observing, scoring, and executing on Polymarket prediction markets. Combines a Node.js API server, two React frontends, a shared Postgres schema, and a Python bot that places live CLOB orders.

## Monorepo Structure

```
.
├── artifacts/
│   ├── api-server/          # @workspace/api-server       — Express API (Node.js)
│   ├── dashboard/           # @workspace/dashboard        — React frontend (Vite)
│   └── mockup-sandbox/      # @workspace/mockup-sandbox   — UI sandbox (Vite)
├── lib/
│   ├── db/                  # @workspace/db               — Drizzle ORM + schema
│   ├── api-spec/            # @workspace/api-spec         — Shared API types
│   ├── api-zod/             # @workspace/api-zod          — Zod validation schemas
│   └── api-client-react/    # @workspace/api-client-react — React Query hooks
├── bot/polymarket-bot/      # Python observer + executor
├── scripts/                 # @workspace/scripts
├── Dockerfile.api            # api-server image
├── Dockerfile.bot            # bot image (pip + requirements.txt)
├── Dockerfile.dashboard      # dashboard image (nginx)
├── Dockerfile.mockup-sandbox # mockup-sandbox image (nginx)
├── railway.api.toml          # Railway deploy config for api-server
├── requirements.txt          # Bot runtime deps (single source of truth)
├── pyproject.toml            # Python project metadata (kept aligned with requirements.txt)
└── pnpm-workspace.yaml       # Node workspace manifest
```

## Prerequisites

- Node.js 24+
- pnpm 9+ (npm/yarn are blocked by the preinstall hook)
- Python 3.11+
- PostgreSQL 16

## Local Development

```bash
# Node side
pnpm install
pnpm --filter @workspace/api-server     dev
pnpm --filter @workspace/dashboard      dev
pnpm --filter @workspace/mockup-sandbox dev
pnpm --filter @workspace/db push        # push DB schema

# Python bot (uses pip with requirements.txt)
cd bot/polymarket-bot
pip install -r ../../requirements.txt
python scheduler.py
```

## Railway Deployment

Each component runs as its own Railway service from this single repo.

| Service          | `RAILWAY_DOCKERFILE_PATH`     | Healthcheck     | Release / Start Command                                    |
|------------------|-------------------------------|-----------------|------------------------------------------------------------|
| `api-server`     | `Dockerfile.api`              | `/api/healthz`  | Release: `pnpm --filter @workspace/db push --force`        |
| `dashboard`      | `Dockerfile.dashboard`        | `/`             | *(nginx default)*                                          |
| `mockup-sandbox` | `Dockerfile.mockup-sandbox`   | `/`             | *(nginx default)*                                          |
| `bot`            | `Dockerfile.bot`              | `/health`       | Start: `python scheduler.py`                               |

### Setup Steps

1. **Provision a PostgreSQL database** in Railway — `DATABASE_URL` is auto-injected into all services that reference it.
2. **Set `RAILWAY_DOCKERFILE_PATH`** as a service variable on each service.
3. **Clear Root Directory** on every service — leave blank so the full repo is the Docker build context.
4. **Bot-only env vars**: configure execution credentials in the bot service before enabling live orders (see [Bot environment variables](#bot-environment-variables)).

---

## Python Bot — Signal & Execution Pipeline

The bot does two things on every cycle:

1. **Observes**: scans the Gamma API, snapshots each watched market, runs strategy engines that emit signals to the `signals` table.
2. **Executes**: if `POLYGON_PRIVATE_KEY` and `BANKROLL_USDC` are set, a separate daemon thread picks up qualified signals and places CLOB orders. Kill switches are available via the HTTP server.

### Pipeline order (every 30 s)

```
market_scanner → data_collector → pnl_tracker → spread_engine
              → neg_risk_engine → tail_yield_engine
              → outcome_tracker → hindsight_logger
```

The `market_scanner` step is gated behind `SCAN_INTERVAL_RUNS` (default: every 12 runs ≈ 6 min). All other steps run every tick.

### Active agents

| Agent                | Role                                                                                  |
|----------------------|---------------------------------------------------------------------------------------|
| `market_scanner`     | Scores and selects up to `MAX_WATCHLIST_SIZE` (default 50) markets from the Gamma API |
| `data_collector`     | Snapshots each watched market into Postgres                                           |
| `pnl_tracker`        | Marks open positions to current market price                                          |
| `spread_engine`      | Flags markets where spread > 2× round-trip fee                                        |
| `neg_risk_engine`    | Detects over-round across multi-outcome events (taker + maker arb variants)           |
| `tail_yield_engine`  | Flags near-certain YES markets (≥ 95¢) with positive hold yield before expiry         |
| `outcome_tracker`    | Resolves short-window signals via snapshot or live API midpoint                       |
| `hindsight_logger`   | Resolves directional signals when markets fully resolve                               |

### Execution layer (daemon thread)

| Module                         | Role                                                                                                |
|--------------------------------|-----------------------------------------------------------------------------------------------------|
| `execution/executor`           | Polls signals, gates them, places orders. NATS fast-path wake; falls back to 10 s timed poll        |
| `execution/order_manager`      | Tick-aware order placement (fail-closed on tick lookup), GTD expiry, reprice on expiry, exit orders. Neg-risk multi-leg with rollback on partial failure |
| `execution/pre_trade_gate`     | Six gates: strategy allowlist, bankroll, freshness, signal- and token-level idempotency, portfolio + position exposure |
| `execution/exit_manager`       | NATS-driven trail / spread-compression / yield-decay exits. Persisted `peak_price` survives restarts. `EXIT_MAX_GIVEBACK_PCT` floor caps slippage |
| `execution/reconciler`         | Periodic DB ↔ CLOB order-state and DB ↔ wallet position-state reconciliation (every ~5 min). Zombie cutoff configurable; CLOB re-check before mark REJECTED |
| `execution/connection_checker` | Two-probe CLOB reachability + wallet auth verification (every ~5 min)                               |
| `scheduler.py` SIGTERM handler | On Railway deploy/restart, cancels every open CLOB order via `cancel_all_open_orders` (bounded 8 s) before process exits |

### Bot HTTP endpoints

`server.py` runs Flask in a background thread, primarily for Replit keep-alive and operator-grade kill switches.

| Endpoint                       | Auth          | Purpose                                                  |
|--------------------------------|---------------|----------------------------------------------------------|
| `GET /`                        | none          | Liveness probe                                           |
| `GET /health`                  | none          | JSON health + DB stats                                   |
| `GET /signals`                 | none          | Last 24 h signals + summary counts                       |
| `GET /watchlist`               | none          | Current watched markets                                  |
| `GET /logs?lines=N&level=...`  | `X-API-Key`   | Tail today's log file                                    |
| `GET /execution/status`        | none          | Paused flag + open order count                           |
| `POST /execution/pause`        | `X-API-Key`   | Halt new signal processing (fill polling continues)      |
| `POST /execution/resume`       | `X-API-Key`   | Resume signal processing                                 |
| `POST /execution/cancel-all`   | `X-API-Key`   | Cancel every open CLOB order                             |

### Bot environment variables

Set these in the Railway UI under the bot service.

| Variable                       | Required for…           | Description                                                                                       |
|--------------------------------|-------------------------|---------------------------------------------------------------------------------------------------|
| `DATABASE_URL`                 | **always**              | PostgreSQL connection string. Auto-injected by Railway's Postgres add-on.                         |
| `BOT_API_KEY`                  | kill switches           | Secret for `X-API-Key` on `/logs` and `/execution/*` POST endpoints. Fail-closed when unset.      |
| `POLYGON_PRIVATE_KEY`          | live execution          | Hex private key. Required to initialise the CLOB client. Without it, the executor doesn't start.  |
| `POLYMARKET_CHAIN_ID`          | live execution          | `137` for Polygon mainnet (default), `80002` for Amoy testnet.                                    |
| `POLYMARKET_SIGNATURE_TYPE`    | **live execution**      | `0` = EOA, `1` = POLY_PROXY, `2` = POLY_GNOSIS_SAFE, `3` = POLY_1271 (Deposit Wallet, default for new polymarket.com accounts). **No default — bot raises on missing.** |
| `POLYMARKET_FUNDER`            | sig_type 1 / 2 / 3      | Proxy / safe / deposit wallet address (NOT your EOA). Also used by `reconcile_positions` to fetch wallet holdings. |
| `BANKROLL_USDC`                | live execution          | Capital allocated to the executor. Bot blocks orders until this is > 0 (configured via dashboard).|
| `EXECUTION_STRATEGIES`         | optional                | Comma-separated allowlist. Defaults to all three engines. Unknown names are silently dropped.     |
| `EXECUTION_MIN_SCORE`          | optional                | Minimum `signal_score` to execute. Default `0.75`.                                                |
| `RECONCILER_ZOMBIE_CUTOFF_MINS`| optional                | Minutes a `PENDING_SUBMISSION` / `SENT` order must be stuck before reconciler marks it REJECTED. Default `30`. Reconciler re-checks the CLOB once before marking. |
| `POLYMARKET_DEBUG_SIGN`        | optional                | If `1`, dumps the full signed EIP-712 payload to logs for diagnosing signature mismatches.        |
| `NATS_URL`                     | optional                | NATS endpoint for fast-path signal wake + exit-manager snapshot stream. No-op when unset.         |
| `SIGNAL_WEBHOOK_URL`           | optional                | HTTP webhook fired on each new signal.                                                            |
| `DISCORD_WEBHOOK_URL`          | optional                | Discord notification webhook for crashes, fills, rejections, reconciler alerts.                   |
| `PORT` / `BOT_PORT`            | optional                | HTTP port. Railway sets `PORT`; `BOT_PORT` is a local fallback (default `8080`).                  |

**Note on L2 credentials**: do NOT set `CLOB_API_KEY`, `CLOB_API_SECRET`, or
`CLOB_API_PASSPHRASE` as env vars. The bot derives them at runtime from the
private key (via `client.create_or_derive_api_key()`) and will ignore any
env-provided values — they are deterministic and re-derived on every cold start.
