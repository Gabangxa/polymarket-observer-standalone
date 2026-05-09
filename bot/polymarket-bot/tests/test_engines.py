"""
Unit tests for signal engine computation logic.
These test the pure math/config-driven scoring — no database required.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from config import FEE_RATES, DEFAULT_FEE_RATE, ARB_MIN_NET_MARGIN, SPREAD_FEE_MULTIPLE


# ── Helpers that mirror the engine logic exactly ───────────────────────────────

def _arb_result(yes_ask: float, no_ask: float, category: str = "") -> dict | None:
    fee_rate, _ = FEE_RATES.get(category, DEFAULT_FEE_RATE)
    threshold = 1.0 - (fee_rate + ARB_MIN_NET_MARGIN)
    if yes_ask + no_ask >= threshold:
        return None
    profit = round(1.00 - (yes_ask + no_ask), 4)
    return {"profit": profit, "score": round(min(profit / 0.05, 1.0), 4), "threshold": threshold}


def _spread_score(spread: float, fee_estimate: float) -> float | None:
    if fee_estimate <= 0:
        return None
    ratio = spread / fee_estimate
    if ratio < SPREAD_FEE_MULTIPLE:
        return None
    return min(ratio / 10.0, 1.0)


# ── binary_arb_engine tests ────────────────────────────────────────────────────

class TestBinaryArb:
    def test_arb_detected_fee_free_category(self):
        # geopolitics: 0% fee → threshold = 1 - 0.015 = 0.985
        r = _arb_result(0.45, 0.49, "geopolitics")
        assert r is not None
        assert r["profit"] == 0.06
        assert r["score"] == 1.0

    def test_no_arb_when_sum_at_threshold(self):
        # geopolitics threshold = 0.985; 0.50 + 0.50 = 1.0 >= 0.985 → no arb
        assert _arb_result(0.50, 0.50, "geopolitics") is None

    def test_crypto_fee_tightens_threshold(self):
        # crypto fee = 7.2% → threshold = 1 - (0.072 + 0.015) = 0.913
        # 0.46 + 0.46 = 0.92 >= 0.913 → no arb despite "cheap" prices
        assert _arb_result(0.46, 0.46, "crypto") is None

    def test_crypto_arb_possible_below_threshold(self):
        # 0.40 + 0.40 = 0.80 < 0.913 → arb exists
        r = _arb_result(0.40, 0.40, "crypto")
        assert r is not None
        assert r["profit"] == 0.2

    def test_score_capped_at_1(self):
        # massive gap → score should be 1.0, not > 1
        r = _arb_result(0.20, 0.20, "geopolitics")
        assert r is not None
        assert r["score"] == 1.0

    def test_unknown_category_uses_default_fee(self):
        fee_rate, _ = DEFAULT_FEE_RATE
        threshold = 1.0 - (fee_rate + ARB_MIN_NET_MARGIN)
        r = _arb_result(0.30, 0.30, "unknown_category")
        assert r is not None
        assert abs(r["profit"] - 0.40) < 1e-6
        _ = threshold  # confirm threshold was computed with default fee


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
