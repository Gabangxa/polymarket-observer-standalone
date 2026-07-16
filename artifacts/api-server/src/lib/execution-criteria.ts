// execution-criteria.ts — the minimum bar a signal must clear to be a
// tradeable opportunity, as surfaced on the dashboard (Signals + Snapshots).
//
// These MIRROR the bot's live executor gate in
//   bot/polymarket-bot/config.py  ->  EXECUTION_MIN_SCORE, EXECUTION_STRATEGIES
// and bot/polymarket-bot/execution/executor.py -> get_executable_signals().
//
// IMPORTANT: this is a deliberate "edge bar only" subset of the real gate.
// The executor ALSO enforces signal freshness (<60s), not-yet-executed, and
// portfolio/position exposure caps. Those are live-execution mechanics — on a
// 7-day observability view they would zero the page out — so they are
// intentionally NOT replicated here. This filter answers "is this a bet-worthy
// opportunity?", not "can the executor fire it this exact second?".
//
// DRIFT RISK: these constants are duplicated across two services in two
// languages (Python bot vs TypeScript API). If config.py changes the threshold
// or the allowlist, update this file to match. There is no shared source.

export const EXECUTION_MIN_SCORE = 0.75;

export const EXECUTION_STRATEGIES = ["spread_engine", "tail_yield_engine"] as const;

// Parse the `executableOnly` query param (accepts "true"/"1", case-insensitive).
export function parseExecutableOnly(value: unknown): boolean {
  if (typeof value !== "string") return false;
  const v = value.toLowerCase();
  return v === "true" || v === "1";
}
