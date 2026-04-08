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
