# PMB-101/102 — LLM-Inferred Dependency Pricing (v3)

**Track:** Trading (real betting capital)
**Status:** v3 — folded in risk-sentinel NOT-YET (5 required changes + Option A conditions) and Sox's
full-strategy-isolation call, 2026-07-15; grounded in repo code. **Re-submitting to risk-sentinel.**
**Author:** spec-analyst
**Pricing fork:** Option A — **RATIFIED-CONDITIONAL** by `risk-sentinel` (conditions in §3.1)
**Strategy isolation:** Sox approved FULL isolation — dependency engine has its own pause flag and
`dep_trade_id`-scoped unwind; it never halts or cancels the live spread_engine / tail_yield_engine.
**Requires sign-off:** `risk-sentinel` (limit values + a dedicated pass on the AC-4a executor change),
`code-guardian` (all code), `deploy-engineer` (Railway env + LLM SDK dep + Drizzle migration)

> This spec was reconstructed after the v2 document was lost. Every claim below is tagged
> **[GROUNDED]** (verified in current repo code) or **[INFERRED]** (from the lost-spec summary,
> not yet in code — treat as a proposal, not a description). Do not build **[INFERRED]** items
> as if they already exist.

---

## 0. Reality check — what the summary got wrong

The task summary described the strategy and the pre-trade gate as if large parts already
existed. They do not. Correcting the record so the spec is honest:

- **[GROUNDED]** `pre_trade_gate.check()` enforces exactly **6 gates**, none of them
  resolution-related:
  1. strategy allowlist (`EXECUTION_STRATEGIES`)
  2. bankroll configured (`BANKROLL_USDC > 0`)
  3. signal freshness — **Gate 3**, `MAX_SIGNAL_AGE_SECS = 60`, applied to every signal that
     carries an `emitted_at` (skipped only if `emitted_at` is absent)
  4. idempotency (`order_exists_for_signal`)
  5. portfolio exposure cap (`MAX_PORTFOLIO_PCT = 0.33`)
  6. per-position exposure cap (`MAX_POSITION_PCT = 0.10`)
  There is **no** resolution-sanity, net-edge, capital-preservation, or resolution-risk check
  in the code today. The summary's "reprice bypasses resolution-sanity/net-edge/…" is describing
  gates that do not exist yet.
- **[GROUNDED]** `check_reprice()` actually skips: strategy allowlist, **freshness (Gate 3)**,
  and idempotency. It keeps only: bankroll, portfolio exposure, per-position exposure. The real,
  present-day bypass gap is **freshness**, not resolution logic.
- **[GROUNDED]** `WILD_COHERENCE_PATTERNS` and `DEP_SIM_THRESHOLD` **do not exist anywhere** in
  the repo. The claim that "the repo already does category/entity-token overlap via
  `WILD_COHERENCE_PATTERNS` with no LLM" is false. The closest existing no-LLM mechanism is
  `MICRO_EVENT_KEYWORDS` in `config.py` (category keyword tagging against the question string).
- **[GROUNDED]** No LLM/embedding/vector code or dependency exists. `requirements.txt` =
  `httpx, psycopg2-binary, flask, nats-py, py-clob-client, pytest`. No `openai`/`anthropic`,
  no vector store, no embedding lib.
- **[GROUNDED]** There is **no "verified" column or status** on the `signals` table
  (`lib/db/src/schema/signals.ts`: `resolved`, `outcome`, `executed`, `pnl` — no verification
  field). The "human hand-edits a Postgres row to verify" workflow is **not in code**. It is a
  notion, not an implementation.
- **[GROUNDED]** `EXECUTION_STRATEGIES = ["spread_engine", "tail_yield_engine"]`. No dependency
  engine is wired into execution. Emitting-only engines that exist: spread, tail_yield, neg_risk,
  odds_shift, binary_arb, micro_spread.
- **[INFERRED]** PMB-099/100/101/102/103 exist only as ticket numbers in the lost spec. None are
  present in code. This is a **greenfield** feature, not an extension of shipped work.

Net: this spec covers building a new strategy end-to-end plus hardening the *existing* gate/reprice
path. Scope accordingly.

---

## 1. Goal

Add a correlated-directional strategy that bets on **LLM-inferred logical dependencies between
prediction markets** (if market A resolves YES, market B is likely to resolve YES/NO), priced so
that a trade is only ever placed when its worst-case joint-resolution payoff is non-negative,
with basis risk charged as an explicit cost — and do it without letting an uncalibrated heuristic
or a hidden infra dependency drive real-money sizing.

---

## 2. Grounded context (source of truth)

**[GROUNDED] Files that define today's execution path:**
- `bot/polymarket-bot/execution/pre_trade_gate.py` — `check()` (6 gates), `check_reprice()`
  (bankroll + 2 exposure checks), `_check_portfolio_exposure`, `_check_position_exposure`.
- `bot/polymarket-bot/execution/executor.py` — `_process_signals` (new-signal path, calls
  `check`), `_reprice_expired_orders` + `_evaluate_reprice` (GTD-expired repost path, calls
  `check_reprice`), `run_executor` loop, `pause()/resume()/is_paused()`.
- `bot/polymarket-bot/config.py` — all constants (bankroll, exposure %, `MAX_SIGNAL_AGE_SECS`,
  `EXECUTION_STRATEGIES`, GTD TTLs, fee tables).
- `bot/polymarket-bot/db.py` — `get_executable_signals` (filters `executed=FALSE`, score, strategy,
  freshness), `get_total_open_exposure` (8-day window), `get_expired_unfilled_orders`, position
  helpers.
- `bot/polymarket-bot/agents/outcome_tracker.py` + `agents/hindsight_logger.py` — write
  `exit_price/pnl/outcome/resolved` back to `signals`. This is the **only existing substrate for
  resolved history** (relevant to PMB-100 calibration).
- `bot/polymarket-bot/alerts.py` — Discord webhook, fire-and-forget (`_send`). This is the only
  paging mechanism; there is no PagerDuty. Existing alert types include `order_rejected`,
  `executor_paused`, `cancel_all_fired`.
- `lib/db/src/schema/signals.ts` — Drizzle signal schema (observer side, Postgres).

**[GROUNDED] Existing kill-switch substrate — and why it is NOT reusable here:**
- `executor.pause()` sets a single **GLOBAL** `threading.Event` (`_PAUSE_FLAG`) that halts new-signal
  processing for **all** strategies (spread_engine + tail_yield_engine included). It cannot pause the
  dependency engine alone → AC-4a introduces a dependency-scoped flag instead.
- `cancel_all_open_orders` (order_manager.py:356) calls `client.cancel_all()`, which is
  **account-wide** and cancels **working orders only** — it does not close FILLED positions. It
  cannot be used for a `dep_trade_id`-scoped unwind → AC-4 requires a scoped, actively-closing
  unwind instead.
The dependency kill-switch therefore builds new mechanism (AC-4/AC-4a), it does not compose these.

---

## 3. Pricing rule — Option A (RATIFIED-CONDITIONAL by risk-sentinel)

Price every dependency trade in two stages. `risk-sentinel` has **ratified Option A subject to the
conditions in §3.1** — those conditions are load-bearing, not optional; the floor's safety depends
on them.

1. **Hard admissibility filter (no EV smoothing).** Enumerate the logical joint-resolution table
   for the (A, B) pair under the inferred relation. Take the **worst-case payoff** across the
   branches where *the dependency holds and both markets resolve consistently*. The trade is
   admissible **only if that worst-case payoff is non-negative**. This is a hard floor; a trade
   that can lose money in a consistent joint resolution is rejected outright, regardless of EV.
2. **EV haircut on the admissible payoff.** From the admissible (non-negative) payoff, subtract an
   EV-style haircut for the **inconsistent-resolution** and **invalid/void** branches. Price those
   branches with a **conservative FIXED default probability constant** (a config constant, e.g.
   `DEP_INCONSISTENCY_PROB_DEFAULT`), **NOT** PMB-102's resolution-risk score. The score is
   uncalibrated; it must not touch sizing.

**Rationale to preserve (do not lose in future edits):** hard non-negative floor prevents ruin
branches; basis risk is charged as an explicit, legible cost; no heuristic drives size. Tightens
later — once PMB-100 has accrued enough resolved dependency outcomes to calibrate the inconsistency
probability against reality, the fixed constant can be replaced with a calibrated estimate (and only
then may a calibrated score influence sizing).

### 3.1 Option A conditions (RATIFIED-CONDITIONAL — load-bearing)

Option A's floor is only safe if these hold. They are conditions of ratification, not nice-to-haves:

1. **Probe-size-until-verified is MANDATORY (not optional; supersedes OQ-3).** An unverified
   LLM-inferred relation is **hard-capped at `DEP_PROBE_SIZE_USDC`** and may **never** trade at full
   size. Reason: the hard floor assumes the relation is real, which makes the inconsistent row
   (X=YES, Y=NO) *logically impossible* and thus safely haircut-able. If the relation is
   **hallucinated**, that row becomes a **normal outcome** — no fixed haircut is conservative for it.
   Full size requires human verification (AC-12); the fixed haircut is only trustworthy on verified
   relations. This makes `DEP_PROBE_SIZE_USDC` a mandatory hard cap, enforced in the gate.
2. **Void allowance covers EITHER market voiding, not one.** The stage-2 haircut's void term must
   price the event that **market A OR market B** (or both) resolve void/invalid — both legs carry
   void risk independently. A single-market void allowance under-prices the branch.

---

## 4. Acceptance criteria

Grouped. Each is independently testable. **[BLOCKING]** items must ship before any real-capital
enablement; **[INFERRED-NEW]** items are new build; **[HARDENING]** items fix the existing path.

### 4.1 Endorsed blocking fixes to the existing path

- [ ] **AC-1 [BLOCKING][HARDENING] Reprice re-check.** `check_reprice()` must re-apply
      resolution-stage/score admissibility for any strategy subject to it — OR the spec must
      explicitly bound and document the gap. Because resolution gating does not exist yet, this AC
      is satisfied by: (a) when the resolution-stage gate (AC-9) lands in `check()`, the same gate
      is also invoked in `check_reprice()`; and (b) independently, `check_reprice()` re-applies a
      freshness check appropriate to reprice (see AC-2). Regression test required: a repriced order
      whose market is in a non-tradeable resolution stage is **rejected**.
- [ ] **AC-2 [BLOCKING][HARDENING] Reprice freshness.** The current freshness bypass is a real gap:
      a GTD-expired reprice re-derives edge from a fresh snapshot but never re-checks staleness.
      `check_reprice()` must reject a reprice whose driving snapshot/signal is older than a
      reprice-appropriate TTL. Test: a reprice built from a stale snapshot is rejected.
- [ ] **AC-3 [BLOCKING] Aggregate dependency-exposure cap.** A bankroll-level cap on total
      capital deployed at full size across **all** LLM-inferred dependency positions
      (`MAX_DEP_PORTFOLIO_PCT` of `BANKROLL_USDC`), enforced in the gate independently of the
      existing 33% portfolio cap. **Measurement basis [pinned, risk-sentinel req #4]:** the new cap
      MUST sum `size_usdc` at the **order level**, exactly like `get_total_open_exposure` (db.py:923)
      — so **both** legs of a dependency trade count (real 2× capital, not a double-count). It must
      NOT sum once per `dep_trade_id`; doing so under-counts real exposure by 2× and creates a
      bypass. Test: the (N+1)th dependency **order** that would breach the cap is rejected while
      non-dependency strategies remain unaffected; and a two-leg trade is verified to consume both
      legs' `size_usdc` against the cap.
- [ ] **AC-4 [BLOCKING] Dependency kill-switch / unwind — strategy-isolated.** [Sox approved full
      strategy isolation; risk-sentinel req #1 + #2.] A control that (a) pauses new
      dependency-signal processing via a **NEW dependency-scoped pause flag** (see AC-4a) and
      (b) unwinds open dependency positions **scoped by `dep_trade_id`** and **actively CLOSING
      filled legs**, then fires an alert.
      - **Do NOT compose `executor.pause()`** — that is a single GLOBAL `threading.Event`
        (`executor._PAUSE_FLAG`) that halts ALL strategies including the live spread_engine and
        tail_yield_engine books. A separate dependency flag is required (AC-4a).
      - **Do NOT rely on `cancel_all_open_orders` (order_manager.py:356)** — it calls
        `client.cancel_all()` which is **account-wide** (would cancel spread/tail_yield orders too)
        and only cancels **working** orders; it does **not** place offsetting orders to close a
        **FILLED** leg. AC-4's unwind must: **(a)** select only orders/positions carrying the target
        `dep_trade_id`(s); **(b)** cancel working legs AND place **offsetting closing orders** for
        already-filled legs; **(c)** never touch non-dependency books.
      Test: invoking it stops new dependency placements, cancels dependency working orders, and
      places offsetting closes for filled dependency legs — while spread_engine/tail_yield_engine
      continue placing and their orders are untouched.
- [ ] **AC-4a [BLOCKING] Dependency-scoped pause flag [modifies shared LIVE execution code —
      staging callout].** Introduce a dependency-only pause flag, checked **inside the dependency
      processing path only**, so pausing dependencies does not set the global `_PAUSE_FLAG` and does
      not halt spread/tail_yield. **This edits `executor.py`, which supervises the two live books.**
      The spec therefore requires: (i) this executor change gets **its own dedicated
      `risk-sentinel` pass** separate from the strategy ratification; (ii) it is staged carefully
      (dry-run / shadow) and verified not to affect the existing books before it ships; (iii) the
      new flag defaults to "dependency paused" until the dependency engine is explicitly enabled.
      Test: setting the dependency pause flag halts only dependency placement; a concurrent
      spread_engine signal still executes.
- [ ] **AC-5 [BLOCKING] Dependency signal-staleness TTL.** A dependency-specific staleness TTL
      (`DEP_SIGNAL_TTL_SECS`) **in addition to** the global `MAX_SIGNAL_AGE_SECS = 60` (Gate 3
      already enforces global freshness). Dependencies resolve on market timescales, so this TTL
      governs how long an *inferred relation* stays actionable, distinct from quote freshness.
      Test: a dependency signal past `DEP_SIGNAL_TTL_SECS` is rejected even if under the global 60s.
      **Additionally [risk-sentinel bound]: a dependency signal missing `emitted_at` must be
      REJECTED (fail-closed).** Gate 3 today skips the freshness check when `emitted_at is None`
      (`if emitted_at is not None`), so an absent timestamp silently bypasses freshness. For
      dependency signals, absence must hard-reject, not pass. Test: a dependency signal with no
      `emitted_at` is rejected by both `check()` and `check_reprice()`.
- [ ] **AC-13 [BLOCKING] Two-leg per-position exposure enforcement — market, token AND side, on
      both gates.** [risk-sentinel req #3.] A dependency trade is TWO legs across TWO markets
      (§13.5). `_check_position_exposure` today (a) checks only `token_ids[0]` of the signal's
      single market and (b) **hardcodes `side="YES"`** (`db.get_position(market_id, token_id,
      "YES")`), and (c) a missing position row **falls through to APPROVE**. Leg B buys the **NO**
      token → looked up as YES → position not found → silently approved. The gate must enforce
      `MAX_POSITION_PCT` on **both** legs' full `(market_id, token_id, SIDE)` key before either
      order is placed. **Both `check()` and `check_reprice()` must apply this two-leg, side-correct
      check** — `check_reprice()` currently calls the same single-leg `token_ids[0]`/YES helper, so
      leg B's per-position cap is unenforced *especially* on reprice. The kill-switch/unwind (AC-4)
      operates on the linked pair (`dep_trade_id`). Test: a dependency trade whose leg-B (NO-side)
      market already sits at the per-position cap is rejected even when leg A has headroom; the same
      rejection holds on the reprice path; unwind closes both legs together.
- [ ] **AC-14 [BLOCKING] One-leg-filled / naked-leg state ownership.** [risk-sentinel req #5.]
      §13.5 flags partial fills in prose but no AC owned it. If one leg fills and the other does not,
      the trade is a **naked directional position**, not the intended hard-floored pair. Two grounded
      hazards: (i) `_reprice_expired_orders` iterates **order-by-order** and would reprice a single
      dependency leg independently → asymmetric re-leg; (ii) nothing today detects the naked state.
      Required: (a) detect one-leg-filled state; (b) resolve it deterministically — place the missing
      leg or close the filled leg (offsetting order, per AC-4 semantics); (c) make dependency-leg
      reprice **`dep_trade_id`-aware** so a single leg is never repriced in isolation; (d) the AC-6
      watcher MUST also fire on the naked-leg state, not only on a resolution-risk spike. Test: a
      simulated one-leg fill triggers detection + resolution and a page; a GTD-expired single dep leg
      is not repriced alone.

### 4.2 Held-position risk-spike watcher (Finding 4)

- [ ] **AC-6 [BLOCKING] Held-position risk watcher.** The spec accepts "capital locked awaiting a
      human." Therefore a watcher **must** summon the human. Deliverable: a watcher over held
      dependency positions + a new `alerts.*` page function + threshold config. It fires (exactly one
      page, with cooldown, mirroring `pipeline_crashed`) on **any** of: (i) a resolution-risk
      indicator spike past threshold; (ii) the **naked-leg state** (AC-14); (iii) at minimum, the
      coarse **`closed` transition** of either leg's market [risk-sentinel bound]. Test: each of the
      three triggers fires exactly one page.

### 4.3 New strategy build (INFERRED — greenfield)

- [ ] **AC-7 [INFERRED-NEW] LLM inference client (PMB-099).** A dependency-inference client that,
      given a candidate market **pair**, returns a relation type + directed outcome legs +
      confidence (see §13 taxonomy). Fail-closed behavior is governed by AC-11, not silent.
      Requires a new dependency (LLM SDK only — **no embedding/vector lib**) — justified to
      `deploy-engineer`, added to `requirements.txt` + Railway env.
      **Candidate-pair triage is DECIDED (OQ-1 resolved): keyword/category-overlap only, extending
      the existing `MICRO_EVENT_KEYWORDS` tagging — NO embedding API, NO vector store, NO
      `DEP_SIM_THRESHOLD`.** The triage stage produces candidate pairs cheaply from shared
      category/entity tokens; the LLM is invoked only on the (small) surviving candidate set, which
      also bounds the inference budget for AC-11.
- [ ] **AC-8 [INFERRED-NEW] Resolved-history accrual for calibration (PMB-100).** Persist resolved
      dependency-trade outcomes (relation held? consistent? void?) so the fixed inconsistency
      constant in §3 can later be calibrated. Substrate exists (`signals.resolved/outcome/pnl` via
      `outcome_tracker`); this AC extends it with dependency-specific fields. No sizing may depend
      on this data until an explicit future calibration ticket.
- [ ] **AC-9 [INFERRED-NEW] Pricing/emit engine (PMB-101).** Implement §3 Option A: hard
      admissibility filter then fixed-constant EV haircut; emit a signal only when the post-haircut
      value clears an edge threshold. Test: a pair whose worst-case consistent payoff is negative
      emits nothing; a pair that clears the floor but not the haircut emits nothing; a pair that
      clears both emits with the haircut applied.
- [ ] **AC-10 [INFERRED-NEW] Resolution-stage enumeration (PMB-103).** An explicit enum of
      non-tradeable stages — `proposed | challenged | dvm_vote` — gating PMB-102's hold logic on
      **this explicit enum**, never on "any non-settled stage" (see §6 #8). **Source resolved
      (OQ-2 finding):** risk-sentinel confirmed via a live Gamma query that the market object carries
      a **`umaResolutionStatuses`** field (alongside coarse `closed`/`closedTime`/`comboStatus`), so
      fine-grained UMA stages are reachable through the **existing `api.py` poller** — **no separate
      UMA subgraph / DVM poller** needed (drop that assumed infra). Staged requirement:
      - **Phase 0/1:** gate on the coarse `closed`/settled distinction Gamma already returns, with
        entry **hard-excluding anything not clearly open** (fail-closed on ambiguous status).
      - **Before Phase-2 scale:** tighten onto the `umaResolutionStatuses` field with the explicit
        enum. The exact stage vocabulary still needs **one sample from a market currently
        mid-resolution** (the sampled settled market returned an empty array) — this is a
        **follow-up confirmation, not new infra**.

### 4.4 Fail-closed / budget behavior (Should-fix #9)

- [ ] **AC-11 [BLOCKING for enablement] Scoring recompute is event-driven + cached + paged on
      failure.** LLM scoring must NOT run per-snapshot (blows budget) and must NOT fail-closed into
      a silent block of every engine. Required: event-driven recompute, cached score components
      reused between events, and an **explicit Discord page** on scoring failure — never a silent
      skip. This is the same failure shape as the crypto desk's "skip-quote-forever" incident:
      a fail-closed gate with no page freezes the book invisibly. On failure it degrades to a defined
      safe state by setting the **dependency-scoped pause flag (AC-4a)** — NOT the global
      `_PAUSE_FLAG` — so spread/tail_yield keep executing. Test: injected scoring failure pages and
      pauses only the dependency engine; other strategies keep executing; no silent global halt.

### 4.5 Human-verify workflow as a real ticket (Should-fix #11)

- [ ] **AC-12 [INFERRED-NEW] Human-verify workflow (PMB-104, new).** Today "verified" = a human
      hand-edits a Postgres row, and **no verification field exists** in the schema. This AC
      specifies it as a real ticket: add a verification field/status to the `signals` (or a
      dependency-trade) table, an observer UI affordance or script to set it, and a gate check that
      an unverified dependency trade is **hard-capped at `DEP_PROBE_SIZE_USDC`** and can never reach
      full size (per §3.1 condition 1 — this is MANDATORY, OQ-3 resolved). Test: an unverified
      dependency signal is capped at probe size (not full size); a verified one may size fully.

---

## 5. Risk-gate section (Trading track — required)

**[GROUNDED] Existing limits (do not weaken):**
- `MAX_POSITION_PCT = 0.10` (per-position), `MAX_PORTFOLIO_PCT = 0.33` (portfolio), enforced in
  `_check_position_exposure` / `_check_portfolio_exposure`.
- `MAX_SIGNAL_AGE_SECS = 60` global freshness (Gate 3).
- `BANKROLL_USDC` env-driven, defaults to `0.0` → execution blocked when unset (fail-safe).

**[INFERRED] New limits — BOUNDS set by risk-sentinel; exact values deferred to Phase-0 data:**
- `MAX_DEP_PORTFOLIO_PCT` — aggregate cap on dependency capital (AC-3). Measured **order-level** so
  both legs count (real 2× capital).
- `DEP_MIN_NET_EDGE` — **bound: `≥ ARB_MIN_NET_MARGIN` (0.015)**, as a fraction of cost. Never looser
  than a pure binary arb, since dependency basis risk is strictly worse (AC-9, §13.6).
- `DEP_INCONSISTENCY_PROB_DEFAULT` — **UNPROVEN / load-bearing / uncalibrated.** Bound: **≥ 0.15** as
  a conservative Phase-0 placeholder; only lowered against the OQ-4 gate (realized inconsistency ≤ the
  constant across ≥ 30 resolved pairs). Do not lower on intuition (§3, AC-8).
- `DEP_SIGNAL_TTL_SECS` — **ratified in principle** (relation-staleness ≠ 60s quote-freshness). Plus:
  a dependency signal **missing `emitted_at` must be REJECTED** (fail-closed), not silently passed
  (AC-5).
- `DEP_PROBE_SIZE_USDC` — **MANDATORY hard cap** for unverified relations (§3.1 cond. 1, AC-12);
  must exist and be enforced in the gate. Full size only after human verification.
- Held-position resolution-risk spike threshold (AC-6) — must at minimum fire (one page, cooldown)
  on the coarse `closed` transition of either leg.

**Kill-switch (AC-4 / AC-4a) — strategy-isolated, NEW mechanism (not the existing global paths):**
a dependency-scoped pause flag (does not touch the global `_PAUSE_FLAG`; does not halt
spread/tail_yield) plus a `dep_trade_id`-scoped unwind that actively closes filled legs (not the
account-wide, working-orders-only `cancel_all_open_orders`). The executor edit that adds the scoped
flag modifies shared LIVE code and requires **its own dedicated `risk-sentinel` pass** and careful
staging before shipping.

**Enablement staging:** no real capital until the pricing engine and all §4.1/§4.2 blocking ACs
pass, the executor pause-flag change clears its dedicated risk-sentinel pass, the new limit values
are set, and the validation plan (§8) clears. First real capital at `DEP_PROBE_SIZE_USDC`;
unverified relations never exceed it.

---

## 6. Should-fixes resolved in this spec

- **#8 — do not gate on "any non-settled stage."** PMB-102's hold logic gates on the explicit
  enum `proposed | challenged | dvm_vote` (AC-10). Gating on "not settled" would block the entire
  book on day one, since nearly all live markets are unsettled. Enum source **resolved (OQ-2)**:
  Gamma's `umaResolutionStatuses` field via the existing poller; coarse settled/closed for Phase
  0/1, tighten onto the field before Phase-2 scale.
- **#9 — scoring budget / fail-closed.** Resolved by AC-11: event-driven recompute, cached
  components, explicit page on failure, no silent global halt.
- **#10 — validation timeline.** See §8. The original 2-week dry-run cannot close because
  dependencies resolve on market timescales; exit criteria are rewritten around resolution events,
  not wall-clock.
- **#11 — human-verify has no ticket.** Resolved by AC-12 (new PMB-104), because it is currently a
  manual Postgres edit with no schema support.

---

## 7. Edge cases & states

- **[GROUNDED] Reprice reuses original `signal_id`** (`_evaluate_reprice` sets
  `result["id"] = order.get("signal_id")`) — idempotency is intentionally skipped on reprice; any
  new gate added to `check_reprice()` must not accidentally re-trip idempotency.
- **[GROUNDED] `emitted_at` absent → Gate 3 is skipped** (`if emitted_at is not None`). A
  dependency signal that omits `emitted_at` would bypass freshness entirely. Dependency signals
  must always carry `emitted_at`; add a test asserting a dependency signal without it is rejected
  (fail-closed), not silently passed.
- **Void / invalid market branch** — priced in the haircut (§3); must never yield a negative
  admissible payoff after the floor.
- **Inconsistent joint resolution** (A YES, B NO against an `A⇒B` relation) — the basis-risk branch;
  the whole point of the haircut. Watcher (AC-6) covers the held-position case where this becomes
  likely mid-flight.
- **LLM scoring failure / timeout** — AC-11: degrade to dependency-engine-paused + page, never
  silent global halt.
- **Aggregate cap reached mid-cycle** — new dependency orders rejected, existing untouched, other
  strategies unaffected (AC-3).
- **Empty/no candidate pairs** — engine emits nothing; no error state (mirror `zero_signal_streak`).

---

## 8. Validation plan (rewrites Should-fix #10)

The 2-week dry-run cannot close on wall-clock because dependency payoffs only realize when the
underlying markets **resolve**, which happens on market timescales (up to `MAX_HOURS_TO_CLOSE = 168h`
per market, and pairs need both legs). Revised:

- **Phase 0 — shadow/dry-run:** engine emits + prices dependency signals, `dry_run` (no orders),
  logging worst-case floor, haircut, and would-be size. Runs until a **statistically meaningful
  number of the paired markets have actually resolved** (event-count exit criterion, not a date).
- **Phase 1 — probe capital:** first real orders at probe size, gated on Phase 0 showing the hard
  floor never went negative on any resolved pair and the fixed haircut was conservative vs realized
  inconsistency rate.
- **Phase 2 — scale:** only after resolved-history accrual (AC-8) is large enough to sanity-check
  the inconsistency constant against reality.
- **Exit criteria are event-driven** (N resolved pairs, realized inconsistency rate within the
  haircut's implied bound), with a wall-clock floor only as a minimum soak, not a target.

**Concrete targets (PROPOSED, OQ-4):** Phase 0 exit = **≥ 30 dependency candidate pairs with BOTH
legs resolved**, with a **3-week minimum soak floor**. Phase 1 probe = `DEP_PROBE_SIZE_USDC` (small
fixed, risk-sentinel to set). Scale gate to Phase 2 = **realized inconsistency rate ≤
`DEP_INCONSISTENCY_PROB_DEFAULT` (≥ 0.15 placeholder) across the ≥ 30 resolved pairs.**

---

## 9. Files likely touched

**[GROUNDED] existing, to modify:**
- `bot/polymarket-bot/execution/pre_trade_gate.py` — AC-1, AC-2, AC-3, AC-5, AC-10 gate hooks.
- `bot/polymarket-bot/execution/executor.py` — **dependency-scoped pause flag (AC-4a)**, dep-scoped
  unwind wiring (AC-4), `dep_trade_id`-aware reprice / naked-leg handling (AC-14), event-driven
  recompute hook (AC-11). **⚠ shared LIVE code — needs its own dedicated `risk-sentinel` pass +
  staged rollout (AC-4a).**
- `bot/polymarket-bot/execution/order_manager.py` — dep-scoped unwind that actively closes filled
  legs (AC-4); `place_order` share-matched two-leg sizing (§13.5).
- `bot/polymarket-bot/config.py` — new constants (§5).
- `bot/polymarket-bot/alerts.py` — new page functions (AC-6, AC-11).
- `bot/polymarket-bot/db.py` — resolved-history fields / queries (AC-8), verification field (AC-12),
  dependency-exposure query (AC-3).
- `lib/db/src/schema/signals.ts` (+ a Drizzle migration) — verification field (AC-12), dependency
  fields (AC-8). **[INFERRED]**
- `requirements.txt` — LLM SDK only (OQ-1 resolved: **no** vector/embedding lib). **[INFERRED]**

**[INFERRED] new:**
- `bot/polymarket-bot/agents/dependency_engine.py` — pricing/emit (PMB-101, AC-9).
- `bot/polymarket-bot/agents/dependency_infer.py` — LLM client (PMB-099, AC-7).
- `bot/polymarket-bot/agents/dependency_watcher.py` — held-position risk watcher (PMB-102/AC-6).
- resolution-stage enum module (PMB-103, AC-10).

---

## 10. Test surface (hand-off to `test-architect`)

- **Unit — gate:** each new gate hook in isolation (AC-1 resolution re-check, AC-2 reprice
  freshness, AC-3 aggregate cap boundary **measured order-level**, AC-5 dependency TTL, AC-10 enum
  rejects proposed/challenged/dvm_vote, passes settled/tradeable). **AC-13:** leg B (NO-side) at cap
  rejects on both `check()` and `check_reprice()`; missing-position-does-not-silently-approve.
  Extend `tests/test_execution_layer.py`.
- **Unit — pricing:** §3 truth table — negative worst-case floor rejects; positive floor but
  haircut kills; both clear emits; void allowance covers **either** market voiding; inconsistent
  branch priced by the fixed constant, not the score. Unverified relation hard-capped at
  `DEP_PROBE_SIZE_USDC` (§3.1).
- **Unit — fail-closed:** AC-11 injected scoring failure pages + sets the **dependency-scoped** pause
  (AC-4a), spread/tail_yield unaffected, no global halt. AC-6 fires one page (cooldown) on spike,
  naked-leg, and coarse `closed`. Dependency signal missing `emitted_at` is rejected on both gates.
- **Integration:** `_reprice_expired_orders` end-to-end — reprice into a non-tradeable stage
  rejected (AC-1), stale-snapshot reprice rejected (AC-2), a lone dep leg is **not** repriced in
  isolation (AC-14). **AC-4a isolation:** setting the dependency pause halts only dependency
  placement while a concurrent spread_engine signal still executes. **AC-4 unwind:** cancels
  dependency working orders AND places offsetting closes for filled dependency legs, scoped by
  `dep_trade_id`, touching no other book. **AC-14:** one-leg-filled state detected + resolved + paged.
- **e2e / dry-run harness:** Phase-0 shadow run emits + prices + logs without placing orders;
  assert no order ever placed and floor never negative on a scripted resolution set.

---

## 11. Reviewers needed

- **`risk-sentinel`** — MANDATORY. Option A ratified-conditional (§3.1). Two remaining passes owed:
  (1) set the limit values within the §5 bounds against Phase-0 data; (2) a **dedicated pass on the
  `executor.py` dependency-scoped pause-flag change (AC-4a)** — it edits shared LIVE execution code
  and must be reviewed + staged separately from the strategy. Nothing goes to real capital without
  both.
- **`code-guardian`** — MANDATORY (all code).
- **`deploy-engineer`** — new dependency (LLM SDK only; OQ-1 resolved = no vector infra), Railway
  env vars, Drizzle migration for schema changes.
- **`test-architect`** — §10 coverage.

---

## 12. Open questions for Sox (do NOT decide these in-spec)

- **OQ-1 [Finding 5 — embedding triage] — RESOLVED 2026-07-15.** Decision: candidate-pair triage =
  **keyword/category-overlap only**, extending existing `MICRO_EVENT_KEYWORDS`. **No** embedding
  API, **no** vector store, **no** `DEP_SIM_THRESHOLD`. The hidden-infra risk (the class the v1→v2
  rewrite killed) is avoided. Reflected in AC-7 and §13.4. No further action for Sox.
- **OQ-2 [#8 enum source] — RESOLVED (risk-sentinel Gamma query).** The Gamma market object carries a
  **`umaResolutionStatuses`** field alongside coarse `closed`/`closedTime`/`comboStatus`, reachable
  through the **existing `api.py` poller** — **no separate UMA subgraph / DVM poller needed** (that
  assumed infra is dropped). AC-10 gates coarse settled/closed for Phase 0/1 (hard-excluding anything
  not clearly open) and tightens onto `umaResolutionStatuses` before Phase-2 scale. **Remaining
  follow-up (not infra, not blocking Phase 0/1):** one sample from a market *currently mid-resolution*
  to confirm the exact stage vocabulary (the sampled settled market returned an empty array).
- **OQ-3 [AC-12 verify semantics] — RESOLVED (risk-sentinel): probe-size-until-verified is
  MANDATORY.** Unverified LLM relations are hard-capped at `DEP_PROBE_SIZE_USDC` and never full size
  (§3.1 cond. 1, AC-12). Not optional — the floor's safety is contingent on the relation being real;
  a hallucinated relation makes the inconsistent loss row a normal outcome, so no fixed haircut is
  conservative for an unverified relation.
- **OQ-4 [#10 timeline] — PROPOSED numbers.** Phase 0 shadow runs until **≥ 30 dependency candidate
  pairs have had BOTH legs resolve** (event-count exit, not wall-clock), with a **3-week minimum
  soak floor** regardless. Phase 1 probe capital = `DEP_PROBE_SIZE_USDC` (small fixed, risk-sentinel
  to set, e.g. order of a few USDC/leg). Gate to scale: **realized inconsistency rate across the 30
  resolved pairs ≤ `DEP_INCONSISTENCY_PROB_DEFAULT`** (i.e. the fixed haircut constant proved
  conservative). All numbers PROPOSED — risk-sentinel / Sox to ratify.
- **OQ-5 [strategy mechanics] — RE-DERIVED as PROPOSED, see §13.** Taxonomy, joint-resolution truth
  table, Option A mapping, edge threshold, and the two-leg→single-token position mapping are now
  specified in §13 as a **proposal pending Sox sign-off**. The position mapping (§13.5) is the
  critical item: it changes how per-position exposure must be enforced (AC-13). Sox must approve
  §13 before AC-9 is built.

---

## 13. Strategy Design (PROPOSED — pending Sox sign-off)

> Everything in this section is **[PROPOSED]**, re-derived from how Polymarket markets actually
> resolve — NOT grounded in existing dependency code (none exists). Sox must approve before AC-9
> is built. Grounded mechanics used as the foundation are cited inline.

**[GROUNDED] Foundation (verified in code):**
- Each Polymarket binary market has two outcome tokens: `token_ids[0]` = YES, `token_ids[1]` = NO
  (`order_manager._token_for_side`). A YES token pays $1 if the market resolves YES, else $0; NO
  token is the mirror.
- Resolution: `resolutionPrice == "1"` → YES, `== "0"` → NO (`hindsight_logger`). Markets can also
  **void/invalid** (neither token pays as expected).
- An order is a single `(market_id, token_id, size_usdc)` GTD order; shares = `size_usdc / price`
  (`order_manager.place_order`).

### 13.1 Relation taxonomy [PROPOSED]

Keep it minimal. Every admissible relation must impose a **hard logical constraint** between two
outcome legs, because Option A's stage-1 floor needs a row that is *logically impossible* to exclude
from the worst case. The single primitive is a **directed implication between two outcome legs**;
the named relations are all reductions of it:

| Relation | Definition | Reduces to | Inconsistent (excluded) row |
|---|---|---|---|
| **Implication** `X ⇒ Y` | leg X of market A resolving YES forces leg Y of market B YES | primitive | X=YES, Y=NO |
| **Equivalence** `X ⇔ Y` | A and B resolve identically on chosen legs | `X⇒Y` **and** `Y⇒X` | X≠Y |
| **Mutual exclusivity** `not (X and Y)` | at most one of X, Y resolves YES | `X ⇒ ¬Y` (implication into the negated leg) | X=YES, Y=YES |

Here a "leg" is a specific outcome token of a market (A-YES, A-NO, B-YES, B-NO); implication is
directed over legs, so mutual exclusivity is just implication into the opposite token. Three named
types, one primitive — small and buildable.

**Explicitly EXCLUDED from v1: conditional / correlational relations** (e.g. "P(B|A) is high").
These impose no hard logical constraint — the adverse joint outcome is always *possible*, so the
worst-case consistent payoff is unbounded and can never clear Option A's stage-1 floor. Admitting
them would require sizing off a probability estimate, exactly what Option A forbids. Revisit only
after calibration (PMB-100), and even then only as a haircut refinement, never as an admissibility
basis.

### 13.2 Joint-resolution truth table [PROPOSED]

Work the primitive `X ⇒ Y`. The trade that profits when the market underprices the implication is:
**buy leg Y (the implied token) and buy ¬X (the negation of the antecedent token)** — i.e. buy Y in
market B and buy the opposite token in market A. Pay `cost = price(Y) + price(¬X) = price(Y) + (1 −
price(X))` per matched share. Payoffs per share ($1 if that token resolves YES):

| A-outcome (X) | B-outcome (Y) | Consistent? | Y pays | ¬X pays | Gross payoff | Net (payoff − cost) |
|---|---|---|---|---|---|---|
| X = YES | Y = YES | yes | 1 | 0 | 1 | `price(X) − price(Y)` |
| X = NO  | Y = YES | yes | 1 | 1 | 2 | `1 + price(X) − price(Y)` |
| X = NO  | Y = NO  | yes | 0 | 1 | 1 | `price(X) − price(Y)` |
| X = YES | Y = NO  | **INCONSISTENT** (relation violated) | 0 | 0 | 0 | `−cost` |
| **market A OR B voids** | — | n/a | — | — | uncertain | basis loss, ≈ `−cost` worst case |

- **Worst-case over consistent rows** = gross payoff **1** (rows 1 and 3) → net `price(X) − price(Y)`.
- **Inconsistent row** (X=YES, Y=NO) is the basis-risk branch: relation didn't hold, you lose the
  full outlay (net `−cost`).
- **Void/invalid** branch [risk-sentinel cond. 2]: price the event that **either market A OR market
  B (or both) voids** — both legs carry void risk independently, so the void allowance must cover
  either leg, not one. Treat conservatively as a full-outlay loss unless the venue's void rules
  return principal.

Mutual exclusivity and equivalence produce their own tables by the same construction (equivalence =
buy the two "same-direction" legs and require both prices coherent; mutual-exclusivity = buy Y=¬(the
other's YES)). The engine builds the table generically from the primitive; the three named types are
presets.

### 13.3 Mapping table → Option A [PROPOSED]

- **Stage 1 — hard admissibility (no smoothing):** admissible iff the **worst-case consistent net ≥
  0**, i.e. for `X ⇒ Y`: **`price(X) ≥ price(Y)`**. This is exactly the logical-coherence violation
  — a coherent market must price `P(Y) ≥ P(X)` under `X⇒Y`; when it doesn't, there is a hard-floored
  edge. No trade is admitted if any consistent row is negative.
- **Stage 2 — EV haircut:** `net_edge = worst_case_consistent_net − haircut`, where
  `haircut = DEP_INCONSISTENCY_PROB_DEFAULT × cost` (inconsistent branch loses the whole outlay)
  **plus** a void allowance. `DEP_INCONSISTENCY_PROB_DEFAULT` is the fixed conservative constant from
  §3 — **never** PMB-102's uncalibrated score. Emit only if `net_edge ≥ DEP_MIN_NET_EDGE`.

### 13.4 Candidate-pair triage [PROPOSED, OQ-1 decided]

Generate candidate pairs from **keyword/category overlap** built on the existing `MICRO_EVENT_KEYWORDS`
category tagging (config.py) plus shared entity tokens in the question strings. No embedding, no
vector store, no `DEP_SIM_THRESHOLD`. Only the small surviving candidate set is sent to the LLM
(AC-7), which bounds inference cost and satisfies AC-11's budget concern.

### 13.5 Two-leg → single-token position mapping [PROPOSED — CRITICAL]

A dependency trade is **two legs in two markets**. The gate and executor model a position as a single
`(market_id, token_id)`. Chosen representation: **two linked single-token orders**, because that is
the only representation that maps to CLOB reality (one order = one token in one market) and composes
with the existing order-level portfolio-exposure sum.

- **Legs:** Leg 1 = BUY leg-Y token in market B; Leg 2 = BUY ¬X token in market A. Both carry a
  shared `dep_trade_id` (new field on `orders` / in signal metadata).
- **Share-matched, not USDC-matched:** the arb requires **equal share counts** across legs (payoff is
  per-share $1), so the two legs will have **different `size_usdc`** (prices differ). Sizing must
  match shares, then derive each leg's USDC. [PROPOSED — flag for review; easy to get wrong.]
- **Portfolio exposure (33%) [GROUNDED interaction]:** `get_total_open_exposure` sums `size_usdc` at
  order level, market-agnostic — so both legs count automatically. Consequence: **one dependency
  trade consumes ~2× the USDC of a single-leg trade** against the 33% cap. Acceptable but must be
  documented so the aggregate dependency cap (AC-3) is set with this in mind.
- **Per-position exposure (10%) [GROUNDED gap → AC-13]:** current `_check_position_exposure` checks
  only `token_ids[0]` of the signal's single market **and hardcodes `side="YES"`**, and a missing
  position falls through to APPROVE. Leg B buys the **NO** token → looked up as YES → not found →
  silently approved. AC-13 requires the gate to check `MAX_POSITION_PCT` on **both** legs' full
  `(market_id, token_id, SIDE)` key, on **both `check()` and `check_reprice()`**.
- **Kill-switch / unwind (AC-4):** `dep_trade_id`-scoped, **actively closing filled legs** (offsetting
  order) — not the account-wide, working-orders-only `cancel_all_open_orders`.
- **Partial-fill / naked-leg risk → owned by AC-14 (BLOCKING).** If leg 1 fills and leg 2 does not,
  you hold a **naked directional position**, not the hard-floored pair. Two grounded hazards:
  `_reprice_expired_orders` iterates order-by-order and would reprice a single leg independently
  (asymmetric re-leg), and nothing detects the naked state today. AC-14 owns detection + deterministic
  resolution + `dep_trade_id`-aware reprice; AC-6's watcher also fires on the naked-leg state.

### 13.6 Edge threshold [PROPOSED]

Express minimum net edge in the same units the existing gate/economics use — a **fraction of capital
deployed (cost)**, consistent with `ARB_MIN_NET_MARGIN = 0.015` and `EV_MIN_THRESHOLD = 0.03`
(config.py). Introduce `DEP_MIN_NET_EDGE` (fraction of `cost`); emit only when
`net_edge / cost ≥ DEP_MIN_NET_EDGE`. Value set by risk-sentinel; propose starting no looser than
`ARB_MIN_NET_MARGIN` since dependency basis risk is strictly worse than a pure binary arb.
