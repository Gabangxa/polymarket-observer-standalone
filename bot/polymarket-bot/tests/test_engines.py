"""
Unit tests for signal engine computation logic.
These test the pure math/config-driven scoring — no database required.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from config import SPREAD_FEE_MULTIPLE


# ── Helpers that mirror the engine logic exactly ───────────────────────────────

def _spread_score(spread: float, fee_estimate: float) -> float | None:
    if fee_estimate <= 0:
        return None
    ratio = spread / fee_estimate
    if ratio < SPREAD_FEE_MULTIPLE:
        return None
    return min(ratio / 10.0, 1.0)


# ── spread_engine tests ────────────────────────────────────────────────────────

class TestSpreadEngine:
    def test_spread_below_multiple_returns_none(self):
        # spread = 0.01, fee = 0.02 → ratio = 0.5 < SPREAD_FEE_MULTIPLE (2.0)
        assert _spread_score(0.01, 0.02) is None

    def test_spread_exactly_at_multiple_passes(self):
        # ratio = 2.0 exactly → should produce a score
        score = _spread_score(0.04, 0.02)
        assert score is not None
        assert score > 0

    def test_spread_score_capped_at_1(self):
        # very large ratio → score capped at 1.0
        assert _spread_score(1.0, 0.001) == 1.0

    def test_zero_fee_estimate_returns_none(self):
        # division by zero guard
        assert _spread_score(0.05, 0.0) is None
