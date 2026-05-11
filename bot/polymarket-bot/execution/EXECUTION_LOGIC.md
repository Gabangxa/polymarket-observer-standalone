# Execution Layer — Logic Documentation

Last updated: 2026-05-10
Covers: `pre_trade_gate.py`, `order_manager.py`, `executor.py`, `scheduler.py`, `nats_bus.py`

---

## Overview

The execution layer is a daemon thread that runs alongside the observer pipeline inside a single Railway Docker container. It reads from the Postgres `signals` table (written by the strategy engines), validates each signal through a series of pre-trade checks, places orders on the Polymarket CLOB, and tracks fills back into the `orders` and `positions` tables.

```
scheduler.py (main process)
├── Flask server thread        ← health / API endpoints
├── NATS bus daemon thread     ← pub/sub bridge (publish + subscribe)
├── Executor thread            ← signals → orders → fills
│   ├── pre_trade_gate.py      ← validation before any API call
│   ├── order_manager.py       ← CLOB interaction, state machine
│   └── auth.py                ← ClobClient singleton
└── Pipeline loop              ← observer engines (unchanged)

NATS subjects published by execution layer:
  pm.execution.placed.{strategy}.{market_id}
  pm.execution.filled.{strategy}.{market_id}
  pm.execution.rejected.{strategy}.{market_id}
  pm.execution.repriced.{strategy}.{market_id}
  pm.heartbeat.executor

NATS subscriptions consumed by executor:
  pm.signals.>  → fast-path wake (_SIGNAL_EVENT.set())
```

The executor is a **dead no-op** unless both `BANKROLL_USDC > 0` and `POLYGON_PRIVATE_KEY` are set in Railway service variables. This makes the deployment safe by default.

---

## scheduler.py — Process Bootstrap

### Responsibility
Entry point for the entire bot process. Runs under Docker `CMD ["python", "scheduler.py"]`.

### Startup sequence (order matters)
1. **Logging configured** — before any imports that use loggers, to avoid missed early-startup messages.
2. **Flask server thread started** — health and API endpoints come up immediately.
3. **Executor thread conditionally started** — checks `BANKROLL_USDC > 0` AND `POLYGON_PRIVATE_KEY` in env. Both must be present; either absent keeps execution disabled and logs a WARNING.
4. **Pipeline loop begins** — runs `run_pipeline()` on a 30s interval; every 12th run includes a full market scanner refresh.

### Guard logic for executor startup
```python
if BANKROLL_USDC > 0 and os.environ.get("POLYGON_PRIVATE_KEY"):
    start_executor()
else:
    logger.warning("Executor NOT started — ...")
```
This is the only place execution is conditionally activated. Removing either env var disables execution without any code change.

### Pipeline cadence
- Run 1, 13, 25, ... → full run (scanner + collector + all engines)
- All other runs → collect + analyse only (no scanner, faster)
- `SCAN_INTERVAL_RUNS = 12` → scanner fires roughly every 6 minutes at 30s poll

---

## executor.py — Execution Daemon Thread

### Responsibility
The executor is the top-level coordinator of the execution loop. It runs forever as a daemon thread, waking every `EXECUTOR_POLL_SECS` (default 10s) to process new signals and check open orders for fills.

### Loop structure
```
while True:
    placed = repriced = 0
    1. Initialise CLOB client if not yet ready
    2. _process_signals(client)        ← new signals → orders (skipped when paused)
    3. _poll_open_orders(client)       ← open orders → fill updates (always runs)
    4. _reprice_expired_orders(client) ← GTD-expired → re-evaluate → repost
    5. nats_bus.publish("pm.heartbeat.executor", {...})
    6. _SIGNAL_EVENT.wait(timeout=EXECUTOR_POLL_SECS)  ← fast-path wake or timed
    7. _SIGNAL_EVENT.clear()
```

**Fast-path wake**: The executor subscribes to `pm.signals.>` via NATS. When a strategy engine publishes a new signal, `_on_signal_message()` calls `_SIGNAL_EVENT.set()`, waking the executor immediately. Without NATS (or when NATS is disconnected), `wait()` times out after `EXECUTOR_POLL_SECS` — the timed poll remains the fallback.

### _process_signals
- Calls `db.get_executable_signals()` — fetches unexecuted signals above `EXECUTION_MIN_SCORE`, within the allowed strategy set, younger than `MAX_SIGNAL_AGE_SECS`.
- Signals are returned ordered by `signal_score DESC` — best opportunity is processed first.
- For each signal: runs `pre_trade_gate.check()`. On rejection, logs reason. Stale signals are immediately marked `executed=TRUE` so they stop appearing in future queries.
- On gate approval: calls `order_manager.place_order()`. Logs outcome.
- **Per-signal isolation**: each signal is processed in a try/except at the loop level. One failure does not abort processing of remaining signals.

### _poll_open_orders
- Calls `db.get_open_orders()` — all orders in non-terminal states: `PENDING_SUBMISSION`, `SENT`, `OPEN`, `PARTIALLY_FILLED`.
- For each open order: calls `order_manager.poll_order_status()` with individual exception handling. A failed poll does not abort the loop.

### CLOB client lifecycle
- `client` starts as `None`. On each cycle, if `None`, `get_client()` is called.
- If `get_client()` raises (bad key, network down), the exception is caught, logged, and the cycle sleeps and retries.
- On auth/connection errors detected mid-cycle, `client` is reset to `None` so the next cycle re-derives credentials.

### Daemon thread behaviour
- Thread is `daemon=True` — it exits when the main process exits. No manual shutdown needed.
- Crashes inside the loop are caught at the top-level `except Exception`, logged with full traceback, and the loop continues after sleeping.

---

## pre_trade_gate.py — Pre-Trade Validation

### Responsibility
Validates a signal against six ordered checks before any order is placed or any DB write occurs. Returns `(bool, str)` — approved flag and rejection reason.

### Design principle: fail-fast, cheapest first
Checks are ordered so the most expensive (DB queries) only run if all cheap checks pass. A rejection at gate 1 costs zero DB calls.

### Gate sequence

| Gate | Check | DB? | Reject condition |
|------|-------|-----|-----------------|
| 1 | Strategy in allowlist | No | Strategy not in `EXECUTION_STRATEGIES` (`spread_engine`, `tail_yield_engine`) |
| 2 | Bankroll configured | No | `BANKROLL_USDC <= 0` |
| 3 | Signal freshness | No | `emitted_at` age > `MAX_SIGNAL_AGE_SECS` (default 60s) |
| 4 | Idempotency | Yes (orders) | An order row already exists for this `signal_id` |
| 5 | Portfolio exposure cap | Yes (orders) | Total open exposure >= `MAX_PORTFOLIO_PCT × BANKROLL_USDC` (default 33%) |
| 6 | Per-position exposure cap | Yes (positions) | `total_bought + working_buy >= MAX_POSITION_PCT × BANKROLL_USDC` (default 10%) |

### Idempotency (Gate 4)
`db.order_exists_for_signal(signal_id)` queries `orders WHERE signal_id = %s`. If a row exists — regardless of its status — the gate rejects. This prevents double-ordering if:
- The executor crashes between `place_order()` and `mark_signal_executed()`
- The signal reappears in the next poll cycle before the DB update propagates

### Exposure check (Gate 6)
Uses `Decimal` arithmetic:
```python
_MAX_EXPOSURE = Decimal(MAX_POSITION_PCT) × Decimal(BANKROLL_USDC)
total_exposure = Decimal(position["total_bought"]) + Decimal(position["working_buy"])
if total_exposure >= _MAX_EXPOSURE: reject
```
`working_buy` is included because pending (unfilled) orders represent real capital at risk. Checking only `total_bought` would allow sending multiple orders for the same market that cumulatively exceed the position cap.

### Rejection behaviour
- Rejected signals are **not** marked `executed=TRUE` (except stale signals — see executor).
- They remain eligible for the next cycle. This allows transient conditions (position count temporarily at max) to self-resolve.
- Stale signals are the exception: they are marked executed in the executor after a stale rejection, since freshness will never recover.

---

## order_manager.py — CLOB Order Placement and Fill Tracking

### Responsibility
Handles all interaction with the Polymarket CLOB API. Owns the order state machine, generates `clOrdId`s, retries API calls, and keeps the `orders` and `positions` tables in sync.

### clOrdId — client order ID
Generated before any API call:
```python
f"{strategy[:8]}_{signal_id}_{timestamp_ms}_{uuid_nonce}"
# e.g. "spread_en_4821_1746870023441_a3f9c12b"
```
- Stored in DB with a `UNIQUE` constraint before the API call.
- On network timeout/retry, the same `clOrdId` is available from the DB row — the CLOB deduplicates on it.
- If the DB insert itself fails (duplicate), the function returns early before touching the API.

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
    CANCELED        ← canceled by us or expired (canceled_at recorded)
    REJECTED        ← CLOB or network error after all retries (error_msg set)
```
Every transition is a DB write via `update_order_status()`. State is always recoverable from the DB.

### place_order — write-before-network pattern
The sequence is deliberately:
1. Insert order row in DB (`PENDING_SUBMISSION`) — idempotency anchor
2. Mark signal `executed=TRUE` — prevents any other cycle claiming the signal
3. Submit to CLOB API
4. Update status to `SENT` → `OPEN`

If step 3 fails after all retries, the order is marked `REJECTED`. The signal is already marked executed — it will not be retried. This is intentional: a signal that could not be executed is a resolved event, not a pending one.

### Strategy pricing
Both strategies currently use the signal's `yes_price` from metadata as the limit price, rounded to 3 decimal places (0.001) using `Decimal.ROUND_DOWN`:

| Strategy | Order type | TIF | GTD window | Price basis | Intent |
|---|---|---|---|---|---|
| `spread_engine` | LIMIT | GTD | 10 min (`ORDER_TTL_SPREAD_SECS`) | `yes_price` (bid side) | Passive — sit at bid, cancel if stale |
| `tail_yield_engine` | LIMIT | GTD | 60 min (`ORDER_TTL_TAIL_SECS`) | `yes_price` (near 1.0) | Aggressive — buy near full payout |

GTD expiration = `now + 60s (Polymarket minimum buffer) + strategy TTL`. On expiry, the order is automatically removed from the CLOB. The executor's `_reprice_expired_orders()` detects expired unfilled orders, re-evaluates whether edge still exists, and reposts if so.

### Size calculation
```python
size_usdc = min(BANKROLL_USDC × kelly_fraction, BANKROLL_USDC × MAX_POSITION_PCT)
```
- Kelly fraction is read from signal metadata (set by `ev_calculator.py` at signal creation time).
- Capped at `MAX_POSITION_PCT × BANKROLL_USDC` regardless of Kelly output.
- Floored at `_MIN_ORDER_USDC = 5.0` USDC (Polymarket's minimum order size).
- Converted to shares: `shares = size_usdc / price`.

### Exponential backoff
```python
delays: 1s → 2s → 4s → 8s → 16s → 30s (cap)
max attempts: ORDER_MAX_RETRIES (default 5)
```
Applies to all three API calls: `create_and_post_order`, `get_order`, `cancel`.
After all retries are exhausted, the last exception is re-raised to the caller.

### poll_order_status — fill tracking
On each executor cycle, all open orders are polled via `client.get_order(exchange_order_id)`.

Fill mapping from CLOB response:
```
size_matched   → filled_qty
size_remaining → working_qty  (remaining unfilled shares)
average_price  → fill_price
status MATCHED/FILLED or remaining==0 → FILLED
status with filled_qty > 0            → PARTIALLY_FILLED
status CANCELED                       → CANCELED
otherwise                             → OPEN
```

Position table is updated with deltas on every fill change:
```python
delta_fill    = new_filled_qty - prev_filled_qty
delta_working = new_remaining  - prev_remaining
db.upsert_position(..., delta_bought=delta_fill, delta_working_buy=delta_working)
```
Using deltas (additive) rather than absolute values prevents race conditions if two poll cycles overlap.

### Decimal arithmetic
All prices and sizes use `decimal.Decimal` throughout. `float` is only used at the boundary when passing values to the py-clob-client API (which expects float). This prevents silent rounding drift on prices like 0.97 or 0.03 that cannot be represented exactly in IEEE 754.

---

## Data flow summary

```
Strategy engine
  → db.insert_signal()           signals table: executed=FALSE

Executor (every 10s)
  → db.get_executable_signals()  fresh, high-score, unexecuted signals
  → pre_trade_gate.check()       6 gates, fail-fast
  → order_manager.place_order()
      → db.insert_order()        orders: PENDING_SUBMISSION + clOrdId
      → db.mark_signal_executed  signals: executed=TRUE
      → CLOB API submit
      → db.update_order_status   orders: SENT → OPEN
      → db.upsert_position()     positions: working_buy += shares

Executor (same cycle, open order poll)
  → db.get_open_orders()
  → order_manager.poll_order_status()
      → CLOB API get_order
      → db.update_order_status   orders: OPEN → PARTIALLY_FILLED → FILLED
      → db.upsert_position()     positions: total_bought += delta_fill
                                            working_buy  -= delta_working
```

---

## Configuration reference

All values are env-var overridable. Defaults are conservative.

| Variable | Default | Effect |
|---|---|---|
| `BANKROLL_USDC` | `0` | Must be set > 0 for executor to start |
| `POLYGON_PRIVATE_KEY` | — | Must be set for executor to start |
| `MAX_POSITION_PCT` | `0.10` | Per-position cap (10% of bankroll) |
| `MAX_PORTFOLIO_PCT` | `0.33` | Total open exposure cap (33% of bankroll) |
| `MAX_SIGNAL_AGE_SECS` | `60` | Reject signals older than 60s |
| `ORDER_TTL_SPREAD_SECS` | `600` | GTD window for spread_engine (10 min) |
| `ORDER_TTL_TAIL_SECS` | `3600` | GTD window for tail_yield_engine (60 min) |
| `ORDER_MAX_RETRIES` | `5` | API retry attempts before REJECTED |
| `EXECUTOR_POLL_SECS` | `10` | Executor fallback poll interval (NATS wake is faster) |
| `EXECUTION_MIN_SCORE` | `0.75` | Min signal score to execute |
| `EXECUTION_STRATEGIES` | `spread_engine, tail_yield_engine` | Phase 1 allowlist (hardcoded) |
| `NATS_URL` | — | NATS connection string — subscriptions and heartbeat are no-ops if unset |

---

## Known limitations (Phase 1)

- **No exit logic**: open positions are held to market expiry. No stop-loss, no price-triggered cancel. Intentional for Phase 1.
- **BUY-only**: all orders are BUY (YES token). NO token execution and spread maker/taker pairing are Phase 2.
- **Single token per signal**: `token_ids[0]` is always used. Multi-leg strategies (binary_arb, neg_risk) are intentionally excluded.
- **No order book depth check at execution time**: price used is from the signal snapshot, which may be up to `MAX_SIGNAL_AGE_SECS` stale. Slippage is not modelled at submission time.
- `_MAX_PORTFOLIO_EXPOSURE` and `_MAX_EXPOSURE_PER_POSITION` are computed at module import time. A change to `BANKROLL_USDC` at runtime requires a process restart.
- **NATS Core = at-most-once**: the fast-path wake and execution event publishes use NATS Core. If the NATS connection is down when a signal fires, the wake message is lost and the executor falls back to the timed poll. No messages are buffered or replayed.

---

## Phase 3: NATS JetStream Upgrade

**Why this matters**: Once Phase 1 is validated and the directional edge is confirmed, upgrading from NATS Core to JetStream is the highest-leverage infrastructure improvement available.

### What JetStream provides that Core cannot

| Capability | NATS Core | JetStream |
|---|---|---|
| Message delivery guarantee | At-most-once | At-least-once |
| Persistence | No | Yes — messages buffered in streams |
| Replay | No | Yes — consumer can replay from offset |
| Durable consumers | No | Yes — survive reconnects |
| Acknowledgement | No | Yes — unacked messages are redelivered |
| Dead letter / max-deliver | No | Yes |

### What this unlocks operationally

- **Execution queue as JetStream stream**: signals can be published to a `pm.execution` stream with durable consumers. If the executor crashes and restarts, it picks up from where it left off — no signals dropped, no duplicates (idempotency key `clOrdId` is still the safety net).
- **Audit trail built into the bus**: every execution event (placed, filled, rejected, repriced) is retained in a JetStream stream, queryable after the fact. No need to dump DB logs to reconstruct order history.
- **Reliable heartbeat monitoring**: heartbeats can be consumed by a health-check service that alerts on missed beats. With Core, if the consumer is disconnected, beats are silently dropped.
- **Cross-service fan-out**: a future analytics service, MM quoting service, or position manager can subscribe to `pm.execution.*` with its own durable consumer without any changes to the execution layer.

### Subjects to stream (proposed stream: `PM_EXECUTION`)

```
pm.execution.placed.>
pm.execution.filled.>
pm.execution.rejected.>
pm.execution.repriced.>
pm.heartbeat.executor
```

### Code changes required

1. **nats_bus.py**: the `publish()` function needs to call `nc.publish()` on a JetStream context (`js = nc.jetstream()`; `await js.publish(subject, payload)`). Subscriptions become durable consumers via `js.subscribe(subject, durable=name, stream=stream_name)`.
2. **Railway infra**: requires a NATS server with JetStream enabled. Options: Synadia Cloud (managed, cheapest path), self-hosted NATS on Railway (add a NATS service), or Railway's native NATS plugin if available at the time of upgrade.
3. **Ack handling in executor**: `_on_signal_message()` should ack the message after the cycle completes to prevent redelivery on crash. Requires passing the `msg` object through, not just `subject` and `data`.

### When to do this

After Phase 1 validation:
- At least 30 days of live execution with no critical bugs
- Signal → order → fill pipeline confirmed end-to-end
- Directional edge confirmed (or refuted) with real capital

Do not upgrade to JetStream mid-phase — it changes the delivery semantics of the bus and requires coordinated testing. Run Phase 1 to completion first.
