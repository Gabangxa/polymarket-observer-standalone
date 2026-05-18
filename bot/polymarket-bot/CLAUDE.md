# CLAUDE.md — Polymarket Bot Constitution

## HARD CONSTRAINTS (read first, always)

1. **Real orders are placed.** This bot signs and posts orders to the
   Polymarket CLOB under `execution/`. Treat every change to that
   subdirectory as a change with real-money blast radius. Read the
   relevant module end-to-end before editing it.
2. **Never store credentials in code or flat files.** The wallet private
   key lives in `POLYGON_PRIVATE_KEY` env var only. L2 API creds are
   derived at runtime from the PK — never persisted, never logged.
3. **Respect rate limits.** Public reads go through `api.py` with bounded
   polling. CLOB writes go through `execution/order_manager.py` which
   applies exponential backoff (1→30s cap, max 5 retries) and classifies
   non-retryable errors so we don't hammer on validation failures.
4. **Postgres is the source of truth.** `signals.executed`, `orders.status`,
   and `positions` are the durable state. Never infer state from RAM,
   logs, or CLOB responses alone. Every state transition is a DB write.
5. **Fail loudly.** All errors logged at WARNING/ERROR. Never silently
   swallow an exception in the execution path — if you can't classify
   it, raise. Silent defaults are how money gets lost (see TickSizeLookupError).
6. **Decimal for all price/size arithmetic.** Float drift on prices is a
   silent correctness bug. `decimal.Decimal` with `ROUND_DOWN` to the
   per-market tick is the contract; quantize before crossing the API
   boundary.

---

## Project purpose

Observe and execute on Polymarket prediction markets. Three strategies
are wired for live execution; observation pipeline runs continuously.

Alpha comes from market structure (spread, over-round, tail-yield decay),
not from forecasting real-world outcomes. The bot does not predict who
wins — it identifies pricing inefficiencies and captures them.

---

## Strategies (live)

| Strategy | Side | Edge type | Execution |
|---|---|---|---|
| `spread_engine` | LIMIT BUY YES (passive maker, joins top of book) | Structural — spread capture | GTD, exit via trailing stop or spread compression |
| `tail_yield_engine` | LIMIT BUY YES (near 1.0) | Statistical — time-value decay | GTD, exit via yield-decay or trailing stop |
| `neg_risk_overround` | Multi-leg BUY YES (taker) or SELL YES (maker) across outcomes | Structural — overround arb | GTD, no exit (locked at fill across legs) |

Other engines (`binary_arb`, `odds_shift`) emit signals for observation
but are NOT in the execution allowlist — unhedgeable leg risk or needs
a fair-value model before execution is safe.

---

## Architecture (current)

```
bot service (single container, multiple threads)
├── Flask health/API server     — server.py
├── Pipeline loop               — main.py (engines write signals to DB)
├── NATS bus                    — nats_bus.py (telemetry only, never orders)
└── Executor loop               — execution/executor.py
    ├── pre_trade_gate.py       — 6 gates: strategy, bankroll, age, idempotency, exposure, position-count
    ├── order_manager.py        — place/poll/cancel; Decimal; backoff; clOrdId
    ├── exit_manager.py         — NATS snapshot-driven exits (trailing stop, yield decay, spread compression)
    ├── reconciler.py           — every 5min: zombie/orphan/drift detection
    ├── connection_checker.py   — CLOB reachable + wallet authenticated probes
    └── auth.py                 — singleton py-clob-client-v2, derives L2 creds from PK

Postgres
├── signals (executed flag, dedup_hourly index)
├── orders (clord_id UNIQUE, explicit state machine)
├── positions (market_id+token_id+side UNIQUE, additive deltas)
└── bot_config (KV: bankroll_usdc, executor_paused, connection_status)
```

Deployed on Railway. Schema migrates at startup via `db.init_schema()`.

---

## API base URLs

- Gamma API:  `https://gamma-api.polymarket.com` (public — market metadata)
- CLOB API:   `https://clob.polymarket.com` (authenticated — orders, fills)
- Data API:   `https://data-api.polymarket.com` (public — position aggregates for reconciliation)

L1 = wallet private key (EIP-712 signing). L2 = HMAC API creds derived from L1.
Five `POLY_*` headers required on every authenticated CLOB call — the SDK handles this.

---

## Required env vars (bot service)

| Var | Purpose |
|---|---|
| `DATABASE_URL` | Postgres connection string |
| `POLYGON_PRIVATE_KEY` | Wallet PK; executor thread will NOT start without it |
| `POLYMARKET_FUNDER` | Deposit wallet / proxy / safe address (NOT EOA) |
| `POLYMARKET_SIGNATURE_TYPE` | 0=EOA, 1=POLY_PROXY, 2=POLY_GNOSIS_SAFE, 3=POLY_1271 |
| `POLYMARKET_CHAIN_ID` | 137 for Polygon mainnet |
| `BANKROLL_USDC` | Capital denominator for Kelly sizing |
| `EXECUTION_STRATEGIES` | Comma-separated allowlist (default: all three live strategies) |
| `EXECUTION_MIN_SCORE` | Float threshold for signal eligibility (default 0.75) |
| `BOT_API_KEY` | Auth for /execution/{pause,resume,cancel-all} endpoints |
| `NATS_URL` | Optional — fast-path executor wake on signal emit |

L2 API creds (`CLOB_API_KEY`, `CLOB_API_SECRET`, `CLOB_API_PASSPHRASE`) are
**derived at runtime** from the PK. Do not set them as env vars — the bot
re-derives identical values on cold start and ignores env-provided ones.

---

## Coding conventions

- Python 3.11+; package manager `uv` (`uv add <pkg>`, never `pip`)
- `httpx` for public HTTP; `py-clob-client-v2` for authenticated CLOB ops
- `config.py` is the single source of truth for tunable constants
- Pipeline engines are plain functions: `run() -> dict` returning a result summary
- Order state transitions are explicit DB writes — never inferred
- One commit per coherent unit of work; conventional commits (`fix(execution): …`)
