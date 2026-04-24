# polymarket-observer-standalone

A monorepo for observing and displaying Polymarket prediction market data. Built with a Node.js API, React dashboards, and a Python bot.

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
├── bot/                     # Python Polymarket bot (standalone)
├── scripts/                 # @workspace/scripts
├── Dockerfile.api            # Builds api-server
├── Dockerfile.dashboard      # Builds dashboard (nginx)
├── Dockerfile.mockup-sandbox # Builds mockup-sandbox (nginx)
└── railway.toml              # Railway deploy config (api-server)
```

## Prerequisites

- Node.js 24+
- pnpm 9+
- PostgreSQL database

## Local Development

```bash
# Install dependencies
pnpm install

# Start API server
pnpm --filter @workspace/api-server dev

# Start dashboard
pnpm --filter @workspace/dashboard dev

# Start mockup sandbox
pnpm --filter @workspace/mockup-sandbox dev

# Push DB schema
pnpm --filter @workspace/db push
```

## Environment Variables

| Variable | Required by | Description |
|---|---|---|
| `DATABASE_URL` | api-server, db | PostgreSQL connection string |

Copy `.env.example` to `.env` and fill in values before running locally.

## Railway Deployment

This repo deploys as multiple Railway services from a single GitHub repo.

### Service Configuration

Each service must be configured in the Railway UI:

| Service | `RAILWAY_DOCKERFILE_PATH` | Healthcheck | Release Command |
|---|---|---|---|
| `api-server` | `Dockerfile.api` | `/api/healthz` | `pnpm --filter @workspace/db push --force` |
| `dashboard` | `Dockerfile.dashboard` | `/` | *(none)* |
| `mockup-sandbox` | `Dockerfile.mockup-sandbox` | `/` | *(none)* |

### Setup Steps

1. **Provision a PostgreSQL database** in Railway — `DATABASE_URL` is auto-injected into all services.
2. **Set `RAILWAY_DOCKERFILE_PATH`** as a service variable on each service (see table above).
3. **Clear Root Directory** on all services — leave it blank so the full repo is the Docker build context.
4. **Clear Start Command** on `dashboard` and `mockup-sandbox` — nginx starts automatically from the Dockerfile.
5. **Clear Release Command** on `dashboard` and `mockup-sandbox` — only `api-server` needs the DB migration.

### Bot Service

The Python bot (`bot/`) is a separate Railway service:

- **Source**: same repo
- **Dockerfile**: `Dockerfile.bot`
- **Root Directory**: *(blank)*

---

## Python Bot — Signal Pipeline

The bot is a read-only observer. It never places orders. It runs a pipeline every `POLL_INTERVAL_SECONDS` (default 30 s) that produces structured signals for manual review.

### Pipeline order

```
market_scanner → data_collector → [signal engines] → outcome_tracker → hindsight_logger
```

| Agent | Role |
|---|---|
| `market_scanner` | Scores and selects up to `MAX_WATCHLIST_SIZE` markets from Gamma API |
| `data_collector` | Snapshots each watched market into Postgres |
| `spread_engine` | Flags spread > 2× round-trip fee |
| `micro_spread_engine` | Flags tighter spread scalp opportunities |
| `neg_risk_engine` | Detects over-round collapse across multi-outcome events |
| `binary_arb_engine` | Detects YES ask + NO ask < 1.0 (guaranteed profit) |
| `tail_yield_engine` | Flags near-certain YES markets with yield before expiry |
| `reversion_engine` | Detects sharp price moves likely to revert |
| `odds_shift_engine` | Detects inter-snapshot price shifts |
| `outcome_tracker` | Resolves short-window signals (spread, arb, micro-spread) using snapshot comparison |
| `hindsight_logger` | Resolves directional + tail-yield signals when markets fully resolve |

### Data collection — light vs. deep

The collector runs in two modes to reduce API load (~60% fewer calls on light runs):

| Fields | Cadence | Consumers |
|---|---|---|
| `midpoint`, `yes_ask`, `no_ask`, `spread`, `fee_rate_bps` | **Every run** | All 7 signal engines |
| `price_history`, `open_interest`, `top_holders`, `recent_trades` | **Every `DEEP_COLLECTION_INTERVAL` runs** (default: 6 ≈ every 3 min) | `reversion_engine` only |

The mode is logged per run (`Collection mode: DEEP / light`) and the run counter survives restarts via `state/deep_collection_counter.json`.

### Outcome tracking

All 7 signal strategies are now tracked:

| Strategy | Tracker | Logic |
|---|---|---|
| `spread_harvesting` | `outcome_tracker` (2 h) | Price stayed within ½ spread of entry |
| `micro_spread_scalp` | `outcome_tracker` (2 h) | Price stayed within ½ spread of entry mid |
| `neg_risk_overround` | `outcome_tracker` (6 h) | Over-round tightened (sum of prices fell) |
| `mean_reversion` | `outcome_tracker` (4 h) | Price moved back from shock direction |
| `binary_arb` | `outcome_tracker` (30 min) | YES ask + NO ask still < 1.0 after window |
| `odds_shift` | `hindsight_logger` | Market resolved in predicted direction |
| `tail_yield_harvest` | `hindsight_logger` | Market resolved YES |

### Bot environment variables

Set these in the Railway UI under the bot service:

| Variable | Required | Description |
|---|---|---|
| `DATABASE_URL` | Yes | PostgreSQL connection string |
| `SIGNAL_WEBHOOK_URL` | No | Webhook endpoint for signal notifications |
| `NATS_URL` | No | NATS server URL for message bus (leave blank to disable) |
| `EXECUTION_MIN_SCORE` | No | Minimum signal score forwarded to `pm.execution.queue` (default `0.75`) |
