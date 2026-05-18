# Execution Layer — Logic Documentation

Last updated: 2026-05-18
Covers: `pre_trade_gate.py`, `order_manager.py`, `executor.py`, `exit_manager.py`,
`reconciler.py`, `connection_checker.py`, `auth.py`, `scheduler.py`, `nats_bus.py`

---

## Overview

The execution layer is a daemon thread that runs alongside the observer pipeline inside a single Railway Docker container. It reads from the Postgres `signals` table (written by the strategy engines), validates each signal through pre-trade checks, places GTD orders on the Polymarket CLOB, tracks fills, manages dynamic exits via NATS snapshot events, and reconciles DB state against the CLOB every five minutes.

```
scheduler.py (main process)
├── SIGTERM/SIGINT handler       ← cancel-all on shutdown (bounded 8s)
├── Flask server thread          ← health / API endpoints
├── NATS bus daemon thread       ← pub/sub bridge (publish + subscribe)
├── Executor thread              ← signals → orders → fills → reprices
│   ├── pre_trade_gate.py        ← validation before any API call (6 gates)
│   ├── order_manager.py         ← CLOB interaction, state machine
│   ├── exit_manager.py          ← NATS snapshot → SELL GTD via trailing stops / yield decay
│   ├── reconciler.py            ← every ~5 min: zombie/orphan/drift detection
│   ├── connection_checker.py    ← every ~5 min: CLOB reachable + wallet auth
│   └── auth.py                  ← ClobClient singleton (derives L2 from PK)
└── Pipeline loop                ← observer engines (unchanged)

NATS subjects published by execution layer:
  pm.execution.placed.{strategy}.{market_id}
  pm.execution.filled.{strategy}.{market_id}
  pm.execution.rejected.{strategy}.{market_id}
  pm.execution.repriced.{strategy}.{market_id}
  pm.execution.exit.{strategy}.{market_id}
  pm.heartbeat.executor

NATS subscriptions consumed by execution layer:
  pm.signals.>             → fast-path wake the executor (_SIGNAL_EVENT.set())
  pm.snapshots.>           → trigger exit_manager evaluation
  pm.execution.filled.>    → refresh exit_manager position index from DB
```

The executor is a **dead no-op** unless `POLYGON_PRIVATE_KEY` is set in Railway service variables. When set, it also requires `POLYMARKET_SIGNATURE_TYPE` (no default — fail-fast on missing) and a non-zero `BANKROLL_USDC` (managed via the dashboard) before any orders are placed.

---

## scheduler.py — Process Bootstrap

### Responsibility
Entry point for the entire bot process. Runs under Docker `CMD ["python", "scheduler.py"]`.

### Startup sequence (order matters)
1. **Logging configured** — before any other imports, so early-startup messages land in `logs/run_*.log`.
2. **SIGTERM/SIGINT handler registered** — before any thread or client init, so a signal during startup still triggers cancel-all.
3. **Flask server thread started** — health and API endpoints come up immediately.
4. **Executor thread conditionally started** — checks `POLYGON_PRIVATE_KEY` in env. If absent: WARNING logged, executor stays disabled.
5. **Pipeline loop begins** — runs `run_pipeline()` on a 30s interval; every 12th run includes a full market scanner refresh.

### Graceful shutdown (`_graceful_shutdown`)
On `SIGTERM` (Railway deploy/restart) or `SIGINT` (Ctrl-C):
- Spawn a daemon thread that calls `order_manager.cancel_all_open_orders(client)`.
- Wait up to `SHUTDOWN_CANCEL_TIMEOUT_SECS = 8.0` for it to complete (Railway sends SIGKILL ~10s after SIGTERM — 2s of headroom).
- A second signal during the wait triggers immediate `sys.exit(1)`.
- After timeout or completion: `sys.exit(0)`.

Without this handler, GTD orders survive a restart on the CLOB while their DB rows can lose their counterpart. New signals on the next boot would then stack against the same bankroll budget, briefly exceeding `MAX_PORTFOLIO_PCT` until the reconciler caught drift ~5 min later.

### Pipeline cadence
- Run 1, 13, 25, ... → full run (scanner + collector + all engines)
- All other runs → collect + analyse only (no scanner, faster)
- `SCAN_INTERVAL_RUNS = 12` → scanner fires roughly every 6 minutes at 30s poll

### Watchdog
On every pipeline iteration, scheduler checks `executor_thread.is_alive()`. A dead executor thread is restarted and `alerts.pipeline_crashed` is fired.

---

## auth.py — CLOB Authenticated Client

Singleton wrapper around `py-clob-client-v2.ClobClient`. Reads `POLYGON_PRIVATE_KEY` once, derives L2 API credentials via `create_or_derive_api_key()` on first call, caches the client in-memory. Thread-safe via double-checked locking.

### Required env vars
| Var | Behaviour if missing |
|---|---|
| `POLYGON_PRIVATE_KEY` | `RuntimeError` on first `get_client()` call |
| `POLYMARKET_SIGNATURE_TYPE` | `RuntimeError` — **no default**, must be set explicitly (0=EOA, 1=POLY_PROXY, 2=POLY_GNOSIS_SAFE, 3=POLY_1271 Deposit Wallet). A wrong default would silently sign every order with the wrong contract address. |
| `POLYMARKET_FUNDER` | `RuntimeError` for sig_type 1/2/3 (deposit/proxy/safe address — not the EOA) |
| `POLYMARKET_CHAIN_ID` | defaults to 137 (Polygon mainnet) |

L2 creds (`CLOB_API_KEY`, `CLOB_API_SECRET`, `CLOB_API_PASSPHRASE`) are **derived at runtime**, not read from env. Setting them as deployment vars is a no-op — the bot re-derives identical values on cold start.

---

## executor.py — Execution Daemon Thread

### Responsibility
Top-level coordinator of the execution loop. Runs forever as a daemon thread, waking every `EXECUTOR_POLL_SECS` (default 10s) to process new signals, poll open orders, reprice expired orders, and periodically run reconciler + connection checks.

### Loop structure
```
while True:
    1. Initialise CLOB client if not yet ready
    2. _process_signals(client)           ← new signals → orders (skipped when paused)
    3. _poll_open_orders(client)          ← open orders → fill updates (always runs)
    4. _reprice_expired_orders(client)    ← GTD-expired → re-evaluate → repost
    5. connection_checker.run_check(...)  ← every Nth cycle
    6. reconcile_orders + reconcile_positions ← every Nth cycle
    7. nats_bus.publish("pm.heartbeat.executor", {...})
    8. _SIGNAL_EVENT.wait(timeout=EXECUTOR_POLL_SECS)  ← fast-path wake or timed
    9. _SIGNAL_EVENT.clear()
```

**Fast-path wake**: subscribes to `pm.signals.>` via NATS. When a strategy engine publishes a signal, `_on_signal_message()` calls `_SIGNAL_EVENT.set()`, waking the executor immediately. Without NATS, `wait()` falls back to the timed poll.

### `_process_signals`
- Calls `db.get_executable_signals()` — unexecuted signals above `EXECUTION_MIN_SCORE`, within `EXECUTION_STRATEGIES`, younger than `MAX_SIGNAL_AGE_SECS`.
- For each signal: runs `pre_trade_gate.check()`. On rejection, logs reason. Stale signals get `mark_signal_executed()` so they stop appearing.
- On approval: calls `order_manager.place_order()`. Result is logged.
- On `place_order` failure with a classifiable skip reason, calls `mark_signal_skipped(reason)` so the dashboard surfaces *why* no order fired. Skip reasons include: `missing_token_id`, `missing_price_metadata`, `price_at_min_tick`, `bankroll_not_set`, `orderbook_not_found`.
- On `orderbook_not_found`, `db.prune_dead_market()` removes the market from the watchlist.
- Per-signal try/except: one failure does not abort processing of remaining signals.

### `_poll_open_orders`
- Calls `db.get_open_orders()` for all non-terminal orders.
- For each: `order_manager.poll_order_status()`. Per-order exception handling.

### `_reprice_expired_orders`
- Detects GTD-expired unfilled orders via `db.get_expired_unfilled_orders()`.
- Re-runs strategy logic against the current snapshot via `_evaluate_reprice()`.
- If edge still exists, calls `pre_trade_gate.check_reprice()` (lighter gate — skips strategy/staleness/idempotency) then re-submits via `place_order(reprice_of=...)`.
- Neg-risk reprices are not implemented; expired neg-risk legs are logged as skipped.

### CLOB client lifecycle
- Lazy init via `get_client()`. On auth/connection error mid-cycle, `client` is reset to `None` so the next cycle re-derives credentials.

### Pause/resume
`pause()`, `resume()`, and `is_paused()` are exposed via the `/execution/pause`, `/execution/resume`, `/execution/cancel-all` endpoints on `server.py`. Pause only blocks `_process_signals` — fill polling, reprice, exit manager, and reconciler continue to run.

---

## pre_trade_gate.py — Pre-Trade Validation

Returns `(approved: bool, reason: str)`. Checks ordered cheapest first; the cheap ones run with zero DB calls.

| Gate | Check | DB? | Reject condition |
|------|-------|-----|------------------|
| 1 | Strategy in allowlist | No | Strategy not in `EXECUTION_STRATEGIES` |
| 2 | Bankroll configured | Yes (config) | `BANKROLL_USDC ≤ 0` |
| 3 | Signal freshness | No | `emitted_at` age > `MAX_SIGNAL_AGE_SECS` (60s) |
| 4 | Idempotency (signal-level) | Yes | Any order row exists for this `signal_id` |
| 4.5 | Idempotency (token-level) | Yes | Any live/filled order exists for this `token_id` |
| 5 | Portfolio exposure cap | Yes | Total open exposure ≥ `MAX_PORTFOLIO_PCT × BANKROLL_USDC` |
| 6 | Per-position exposure cap | Yes | `total_bought + working_buy ≥ MAX_POSITION_PCT × BANKROLL_USDC` |

### Idempotency anchors (Gates 4 + 4.5)
- **Gate 4** is the load-bearing safety against duplicate execution on crash recovery. `_place_neg_risk_legs` and `_place_neg_risk_maker_legs` defer `mark_signal_executed` to the end of their loop; if the process crashes mid-loop, the placed legs leave order rows that Gate 4 detects, blocking re-execution until the signal ages out as stale (≤ `MAX_SIGNAL_AGE_SECS`).
- **Gate 4.5** prevents a second order on the same token from a *different* signal (e.g. spread + tail both firing on the same market).

### `check_reprice` — lighter gate for repriced orders
Skips Gates 1, 3, 4 (strategy/staleness/idempotency irrelevant for repriced orders), runs Gates 2, 5, 6 only.

### Exposure check uses `Decimal`
`working_buy` is included so pending orders consume budget; otherwise multiple BUY orders for the same market could collectively exceed `MAX_POSITION_PCT`.

---

## order_manager.py — CLOB Order Placement and Fill Tracking

### `clOrdId` — client order ID
```
f"{strategy[:8]}_{signal_id}_{timestamp_ms}_{uuid_nonce}"
# e.g. "spread_en_4821_1746870023441_a3f9c12b"
```
- Stored in DB with a `UNIQUE` constraint before the API call. Duplicate insert raises `IntegrityError` → caller skips, no API call.
- On retry, the same `clOrdId` is reused (the CLOB deduplicates on it).

### Order state machine
```
PENDING_SUBMISSION  ← row inserted in DB, before API call
       ↓
     SENT           ← API call dispatched (submitted_at recorded)
       ↓
     OPEN           ← CLOB acknowledged, exchange_order_id set, working_qty set
       ↓
PARTIALLY_FILLED    ← some shares matched (filled_qty > 0, remaining > 0)
       ↓
    FILLED          ← fully matched (filled_at recorded)
    CANCELED        ← canceled by us, expired GTD, or shutdown (canceled_at recorded)
    REJECTED        ← CLOB validation error or all retries exhausted (error_msg set)
```
Every transition is a DB write via `update_order_status()`. State is always recoverable from the DB — never inferred from absence of data.

### Tick size — fail closed
`_tick_dec(client, token_id)` looks up the per-market tick (0.01 standard, 0.001 for tail markets > 0.96 or < 0.04). On lookup failure, raises `TickSizeLookupError` — **no default**. Silent default would either leak edge (rounding 0.987 to 0.98 on a 0.001-tick market) or trigger non-retryable `invalid-tick-size` rejections.

Cache TTLs at `_TICK_CACHE_TTL_SECS = 60.0` so that prices crossing the 0.96/0.04 boundary mid-trade re-resolve within a bounded window. `_invalidate_tick(token_id)` is exposed for explicit invalidation on rejection paths.

### Non-retryable error classification
`_is_retryable(exc)` lowercases the CLOB error message and matches against `_NON_RETRYABLE_ERROR_PATTERNS`. Substrings that fail-fast (no backoff): `invalid order`, `tick size`, `min size`, `insufficient`, `unauthorized`, `signature`, `nonce`, `expired`, `version_mismatch`, `market closed`, `does not exist`, `not found`. Transient errors (timeouts, 5xx) retry with exponential backoff (1s→30s cap, 5 attempts).

### Strategy pricing
| Strategy | Side | TIF | GTD window | Price basis |
|---|---|---|---|---|
| `spread_engine` | LIMIT BUY YES | GTD | 10 min | `yes_ask − 1 tick` (passive maker, joins top of book) |
| `tail_yield_engine` | LIMIT BUY YES | GTD | 60 min | `yes_price` (near 1.0) |
| `neg_risk_overround` (taker) | LIMIT BUY YES per outcome | GTD | 2 min | `yes_ask` per outcome (multi-leg) |
| `neg_risk_overround` (maker) | LIMIT SELL YES per outcome | GTD | 2 min | `yes_price` per outcome (multi-leg) |
| `exit_*` | LIMIT SELL YES | GTD | 60 min | snapshot `yes_price` at exit trigger |

GTD expiration = `now + 60s (Polymarket minimum buffer) + strategy TTL`.

### Neg-risk multi-leg atomicity
`_place_neg_risk_legs` (taker) and `_place_neg_risk_maker_legs` (maker):
1. Loop over outcomes, attempting one CLOB order per outcome.
2. Track `placed_legs: list[(clord_id, exchange_order_id)]` for successful submissions.
3. On partial failure (some legs ACK, one fails): call `_rollback_neg_risk_legs(placed_legs, client)` which best-effort cancels the placed legs. FILLED legs cannot be unwound here — they become real positions for the operator to manage.
4. `mark_signal_executed` is called at the **end** of the function, not before the loop. On crash mid-loop, pre_trade_gate Gate 4 prevents re-execution until the signal ages out; reconciler then picks up any orphan CLOB orders.

### Exits — `place_exit_order`
Posts a LIMIT SELL YES GTD at the snapshot price the exit_manager passed in. Uses `clord_id = _make_clord_id("exit", 0)` (sentinel signal_id=0 — exits have no originating signal). On failure, the working_sell delta is rolled back so position state stays accurate.

### Cancellation
- `cancel_order(clord_id, exchange_order_id, client)` — single-order cancel via `OrderPayload(orderID=...)`, marks DB CANCELED on success.
- `cancel_all_open_orders(client)` — one CLOB `cancel_all()` call, then DB sync for all open rows regardless of API outcome. Used by `/execution/cancel-all` endpoint and the SIGTERM handler.

### Fill mapping (`poll_order_status`)
```
size_matched   → filled_qty
size_remaining → working_qty
average_price  → fill_price
status MATCHED/FILLED or remaining==0 → FILLED
status with filled_qty > 0            → PARTIALLY_FILLED
status CANCELED                       → CANCELED
otherwise                             → OPEN
```
Position deltas are additive (`new − prev`), not absolute, so overlapping poll cycles are race-safe.

### Decimal arithmetic
All prices and sizes use `decimal.Decimal` with `ROUND_DOWN` to tick. `float` is only used at the API boundary. Size is always quantised to 2 decimal places — Polymarket's `ROUNDING_CONFIG` enforces `size=2` for every tick size; finer is rejected as "Invalid order inputs".

### Exponential backoff
`_backoff_retry(fn)`: 1s → 2s → 4s → 8s → 16s → 30s cap, `ORDER_MAX_RETRIES` attempts (default 5). Non-retryable errors raise immediately without sleeping.

---

## exit_manager.py — Dynamic Exit Manager

NATS-driven. Subscribes to `pm.snapshots.{market_id}` and evaluates per-strategy exit conditions on every snapshot. Position index is seeded from DB at startup and refreshed on `pm.execution.filled.>` events.

### Exit conditions by strategy
- **`tail_yield_engine`**:
  - (1) Trailing stop — fire if `current_price < peak_price − TRAIL_PIPS_TAIL` (0.5¢). Only armed once `peak > avg_cost`.
  - (2) Yield-decay — fire if annualised `(1 − price) / price × (8760 / hours_to_expiry) < TAIL_YIELD_MIN_HOLD_YIELD` (10%).
- **`spread_engine`**:
  - (1) Trailing stop — same shape, `TRAIL_PIPS_SPREAD` = 1¢.
  - (2) Spread-compression — fire if `live_spread < SPREAD_EXIT_FEE_MULTIPLE × estimated_fee` (1.5×). Edge has compressed to near the cost of trading it.
- **`neg_risk_overround`**: no exit. Profit is locked at fill time across all legs; exiting a single leg creates directional exposure.

### `peak_price` persistence
`peak_price` ratchets upward on every snapshot where `current_price > peak`. The new value is persisted via `db.update_peak_price()` using `GREATEST(COALESCE(peak_price, 0), %s)` so concurrent writers (or out-of-order events) converge to the maximum. On startup, `_seed_index` rehydrates `peak_price` from the DB column; if NULL (legacy row or no snapshot since migration), falls back to `avg_cost` — conservative because peak == avg_cost disarms the trailing stop until profit appears.

Without persistence (the pre-2026-05-18 behaviour), a container restart reset `peak` to `avg_cost` and disarmed the trailing stop until price climbed back to a new peak.

### Giveback floor
Before placing the exit, if `current_price < avg_cost × (1 − EXIT_MAX_GIVEBACK_PCT)`, the exit is skipped and re-evaluated next snapshot. Bounds slippage when an exit triggers into a market where the spread has blown out (typical near tail-market expiry). Set `EXIT_MAX_GIVEBACK_PCT = 0` to disable.

### `exit_pending` flag
Mutex against back-to-back snapshot events triggering duplicate exits. Set under lock before any I/O; cleared on exit-order failure.

### `_on_fill` refresh
Subscribes to `pm.execution.filled.>` and refreshes the in-memory entry via `db.get_open_positions_with_strategy(market_id=...)`. Peak preference order: existing in-memory > persisted DB > avg_cost.

---

## reconciler.py — DB ↔ CLOB State Reconciliation

Detection-first. Runs from the executor loop every ~5 min. Never auto-deletes data.

### `reconcile_orders`
Buckets DB-open orders against `client.get_orders()`:
- **`in_sync`** — `status=OPEN`, `exchange_order_id` is in the CLOB set. No action.
- **`terminal`** — `status=OPEN`, missing from CLOB. Re-poll via `poll_order_status`; on failure, mark CANCELED.
- **`zombie`** — `PENDING_SUBMISSION`/`SENT` (or missing EOI) older than `RECONCILER_ZOMBIE_CUTOFF_MINS` (default 30). Before declaring dead, attempt one final `get_order(exchange_order_id)` for any zombie that has an EOI — a slow ACK arriving after the bulk fetch should not be wrongly REJECTED. Recovered zombies are tracked separately as `zombie_saved`.
- **`orphans`** — CLOB has an order whose ID isn't in our DB. Logged + alerted via `alerts.reconciler_orphans`; never auto-canceled (operator must investigate).

### `reconcile_positions`
Compares the bot's per-token `net = total_bought − total_sold` against the Polymarket Data API's wallet positions for `POLYMARKET_FUNDER`. Tolerates dust below `_POSITION_DRIFT_TOLERANCE = 0.01 shares`. Detection only — alerts via `alerts.position_drift`. Operator decides how to resync.

---

## connection_checker.py — CLOB Health Probes

Two-probe check, runs from the executor loop:
1. **CLOB reachable** — `client.get_ok()` (unauthenticated `/ok`).
2. **Wallet authenticated** — `client.get_api_keys()` (requires L2 creds to match the funder's expected signer).

Result is written to `bot_config["connection_status"]` JSON for dashboard display. A failure in probe 1 skips probe 2.

---

## Data flow summary

```
Strategy engine
  → db.insert_signal()              signals table: executed=FALSE

Executor (every 10s OR NATS wake)
  → db.get_executable_signals()     fresh, high-score, unexecuted signals
  → pre_trade_gate.check()          6 gates, fail-fast
  → order_manager.place_order()
      → db.insert_order()           orders: PENDING_SUBMISSION + clOrdId
      → db.mark_signal_executed()   signals: executed=TRUE  (deferred for neg-risk)
      → CLOB API create_and_post    SENT → OPEN
      → db.upsert_position()        positions: working_buy += shares

Executor (same cycle, open order poll)
  → db.get_open_orders()
  → order_manager.poll_order_status()
      → CLOB API get_order
      → db.update_order_status      orders: OPEN → PARTIALLY_FILLED → FILLED
      → db.upsert_position()        positions: total_bought += delta_fill
                                                working_buy  -= delta_working
                                    NATS: pm.execution.filled.{strategy}.{market}

NATS pm.snapshots.{market_id}       (every snapshot write from db.insert_snapshot)
  → exit_manager._on_snapshot()
      → ratchet peak_price (in-memory + db.update_peak_price)
      → evaluate per-strategy exit conditions
      → if triggered AND price ≥ avg_cost × (1 − giveback_pct):
          → order_manager.place_exit_order() — LIMIT SELL YES GTD

Executor (every ~5 min)
  → reconciler.reconcile_orders()    in_sync / terminal / zombie / orphan
  → reconciler.reconcile_positions() detect drift vs Data API
  → connection_checker.run_check()   CLOB reachable + wallet auth

scheduler.py SIGTERM/SIGINT
  → order_manager.cancel_all_open_orders(client)  bounded 8s
  → sys.exit
```

---

## Configuration reference

All values are env-var overridable. Defaults are conservative.

| Variable | Default | Effect |
|---|---|---|
| `POLYGON_PRIVATE_KEY` | — | Required; executor not started without it |
| `POLYMARKET_SIGNATURE_TYPE` | — | **No default**; must be set (0/1/2/3) |
| `POLYMARKET_FUNDER` | — | Required for sig_type 1/2/3 |
| `POLYMARKET_CHAIN_ID` | `137` | Polygon mainnet |
| `BANKROLL_USDC` | `0` (dashboard-managed) | Must be > 0 for orders |
| `MAX_POSITION_PCT` | `0.10` | Per-position cap (10% of bankroll) |
| `MAX_PORTFOLIO_PCT` | `0.33` | Total open exposure cap (33% of bankroll) |
| `MAX_SIGNAL_AGE_SECS` | `60` | Reject signals older than 60s |
| `ORDER_TTL_SPREAD_SECS` | `600` | GTD window for spread_engine (10 min) |
| `ORDER_TTL_TAIL_SECS` | `3600` | GTD window for tail_yield_engine (60 min) |
| `ORDER_TTL_NEG_RISK_SECS` | (see config.py) | Short — leg fill timing matters |
| `ORDER_TTL_EXIT_SECS` | `3600` | GTD window for exit SELL orders |
| `ORDER_MAX_RETRIES` | `5` | API retry attempts before REJECTED |
| `EXECUTOR_POLL_SECS` | `10` | Executor fallback poll interval (NATS wake is faster) |
| `EXECUTION_MIN_SCORE` | `0.75` | Min signal score to execute |
| `EXECUTION_STRATEGIES` | `spread_engine,tail_yield_engine,neg_risk_overround` | Live allowlist |
| `TAIL_YIELD_MIN_HOLD_YIELD` | `0.10` | Yield-decay exit threshold (10% annualised) |
| `SPREAD_EXIT_FEE_MULTIPLE` | `1.5` | Spread-compression exit threshold |
| `TRAIL_PIPS_TAIL` | `0.005` | Trailing-stop pips for tail_yield (0.5¢) |
| `TRAIL_PIPS_SPREAD` | `0.01` | Trailing-stop pips for spread_engine (1¢) |
| `EXIT_MAX_GIVEBACK_PCT` | `0.05` | Refuse exit if `price < avg_cost × (1 − 0.05)` |
| `RECONCILER_ZOMBIE_CUTOFF_MINS` | `30` | Mark zombie after stuck this long (with CLOB re-check first) |
| `NATS_URL` | — | NATS connection — wake + telemetry no-ops if unset |
| `POLYMARKET_DEBUG_SIGN` | — | If `1`, dump full signed EIP-712 payload for diagnosis |

---

## Phase 3: NATS JetStream Upgrade

**Why this matters**: Once the directional edge is confirmed at scale, upgrading from NATS Core to JetStream is the highest-leverage infra improvement available.

### What JetStream provides that Core cannot

| Capability | NATS Core | JetStream |
|---|---|---|
| Message delivery | At-most-once | At-least-once |
| Persistence | No | Yes — streams |
| Replay | No | Yes |
| Durable consumers | No | Yes |
| Acknowledgement | No | Yes |
| Dead letter / max-deliver | No | Yes |

### What this unlocks
- **Execution audit trail in the bus**: every `pm.execution.*` event retained, queryable without dumping DB logs.
- **Reliable heartbeat monitoring**: health-check service alerts on missed beats.
- **Cross-service fan-out**: future analytics / MM / position-manager services subscribe with durable consumers, no changes to the execution layer.

Note: signals are NOT routed through NATS as an execution queue — Postgres remains the source of truth. NATS is telemetry + fast-path wake only. JetStream is an upgrade for the *telemetry* path, not the *order* path.

### When to do this
After 30+ days of live execution with no critical bugs, directional edge confirmed with real capital. Do not upgrade mid-phase — it changes delivery semantics and requires coordinated testing.
