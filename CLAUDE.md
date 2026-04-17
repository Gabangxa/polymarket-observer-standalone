# Polymarket Observer — CLAUDE.md

## What this is
Polymarket prediction-market observer: API server, real-time dashboard, Discord bot, and a mockup sandbox. Python backend + TypeScript frontend monorepo.

---

## Stack

| Layer | Choice |
|---|---|
| Monorepo | pnpm workspace (`pnpm-workspace.yaml`) |
| Backend | Python 3.11+ — Flask 3.0.3, httpx, psycopg2-binary |
| Frontend | TypeScript (see `artifacts/dashboard`) |
| Shared libs | `lib/api-spec`, `lib/api-zod`, `lib/api-client-react`, `lib/db` |
| Package mgr | **pnpm only** — npm/yarn are blocked by preinstall hook |
| Containers | Docker (4 images: api, bot, dashboard, mockup-sandbox) |
| Deployment | Railway (`railway.toml`) |
| Python env | uv (`uv.lock`) |

---

## Key commands

```bash
pnpm install                    # Install all workspace deps
pnpm build                      # Build dashboard + api-server
pnpm typecheck                  # Type-check all packages
pnpm --filter @workspace/api-server run start   # Start API server

# Python (use uv)
uv run python main.py           # Run Python entrypoint
```

---

## Project structure

```
artifacts/
  api-server/        ← Node.js API server
  dashboard/         ← React/TS frontend
  mockup-sandbox/    ← Prototype environment
  polymarket-bot/    ← Discord bot
lib/
  api-spec/          ← OpenAPI / shared spec
  api-zod/           ← Zod schemas (source of truth)
  api-client-react/  ← Generated API client
  db/                ← DB schema + client
scripts/             ← Dev/deploy utilities
main.py              ← Python entrypoint
```

---

## Rules

- `lib/api-zod` is the schema source of truth — changes there propagate to `api-client-react` and `api-spec`.
- Never use npm or yarn — pnpm only (the preinstall hook will exit 1).
- Python deps go through uv (`uv add <pkg>`), not pip directly.
- Each Docker image is independently deployable — keep service coupling loose.

---

## Scope discipline

Before modifying any file, confirm it is **directly required** by the stated task.

- Do not refactor, rename, or standardise adjacent code as a side effect.
- Do not touch working files just to make them consistent with new code.
- If a file is not broken and not blocking the task, leave it alone.
- For any change touching more than 2 files, list every file to be modified and the reason before starting. Cut the list if any entry is not strictly necessary.

**Deployment config is high blast-radius.** Changes to `railway.toml`, Dockerfiles, or build scripts affect live services. Treat these like production changes: verify the exact field names and paths against Railway docs before committing, and confirm the change applies only to the intended service.

**All Railway changes must be verified against Railway documentation.** Before modifying any `railway.toml`, Dockerfile, or Railway-related config, look up the relevant Railway docs to confirm: correct field names and their accepted values, which service the config applies to in a monorepo, and how `preDeployCommand`, health checks, and restart policies actually behave at deploy time. Do not infer Railway behavior from general Docker/CI knowledge — Railway has its own execution model (e.g. `preDeployCommand` runs inside the deployed container image, not a separate build environment).

**Commit early, commit often.** Never let a large batch of changes sit uncommitted. Each logical unit of work (schema change, new engine, UI feature) should be its own commit so failures can be isolated and reverted cleanly.
