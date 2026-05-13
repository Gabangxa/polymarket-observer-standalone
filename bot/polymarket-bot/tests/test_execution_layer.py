"""
Tests for ev_calculator (pure math) and pre_trade_gate (gate logic).

All DB calls in pre_trade_gate are mocked — no Postgres connection required.
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

from agents.ev_calculator import yes_ev, no_ev, _kelly, size_recommendation
from config import EV_MIN_THRESHOLD, KELLY_FRACTION
from execution import pre_trade_gate as ptg


# ── ev_calculator ─────────────────────────────────────────────────────────────

class TestYesEv:
    def test_positive_edge(self):
        # q=0.6, p=0.5 → (0.6 - 0.5) / (1 - 0.5) = 0.2
        assert abs(yes_ev(0.6, 0.5) - 0.2) < 1e-9

    def test_zero_edge_when_q_equals_p(self):
        assert yes_ev(0.5, 0.5) == 0.0

    def test_negative_edge(self):
        # q=0.4, p=0.5 → (0.4 - 0.5) / 0.5 = -0.2
        assert yes_ev(0.4, 0.5) < 0

    def test_p_at_zero_returns_zero(self):
        assert yes_ev(0.5, 0.0) == 0.0

    def test_p_at_one_returns_zero(self):
        assert yes_ev(0.5, 1.0) == 0.0

    def test_q_at_zero_returns_zero(self):
        assert yes_ev(0.0, 0.5) == 0.0

    def test_q_at_one_maximum_edge(self):
        # q=1.0, p=0.5 → (1 - 0.5) / 0.5 = 1.0
        assert abs(yes_ev(1.0, 0.5) - 1.0) < 1e-9

    def test_near_certainty_large_edge(self):
        # p=0.05, q=0.50 → (0.50-0.05)/(1-0.05) ≈ 0.474 — substantial edge
        ev = yes_ev(0.50, 0.05)
        assert ev > 0.4


class TestNoEv:
    def test_delegates_to_yes_ev_complement(self):
        # no_ev(q, p) == yes_ev(1-q, 1-p)
        assert abs(no_ev(0.4, 0.5) - yes_ev(0.6, 0.5)) < 1e-9

    def test_positive_edge_when_yes_overpriced(self):
        # market prices YES at 0.7 but true prob is 0.4 → NO has edge
        assert no_ev(0.4, 0.7) > 0

    def test_zero_edge_at_fair_price(self):
        assert no_ev(0.5, 0.5) == 0.0

    def test_negative_edge_when_no_underpriced(self):
        assert no_ev(0.6, 0.3) < 0


class TestKelly:
    def test_positive_kelly_with_edge(self):
        k = _kelly(0.6, 0.5)
        assert 0 < k <= 1.0

    def test_zero_kelly_at_fair_price(self):
        assert _kelly(0.5, 0.5) == 0.0

    def test_negative_edge_clamped_to_zero(self):
        assert _kelly(0.3, 0.7) == 0.0

    def test_p_at_zero_returns_zero(self):
        assert _kelly(0.5, 0.0) == 0.0

    def test_p_at_one_returns_zero(self):
        assert _kelly(0.5, 1.0) == 0.0

    def test_q_at_zero_or_one_returns_zero(self):
        assert _kelly(0.0, 0.5) == 0.0
        assert _kelly(1.0, 0.5) == 0.0

    def test_fraction_scales_linearly(self):
        full  = _kelly(0.7, 0.5, fraction=1.0)
        half  = _kelly(0.7, 0.5, fraction=0.5)
        quart = _kelly(0.7, 0.5, fraction=0.25)
        assert full > 0
        assert abs(half  - full * 0.5)  < 1e-6
        assert abs(quart - full * 0.25) < 1e-6

    def test_default_fraction_matches_config(self):
        # _kelly with no fraction arg should use KELLY_FRACTION from config
        k_default  = _kelly(0.65, 0.5)
        k_explicit = _kelly(0.65, 0.5, fraction=KELLY_FRACTION)
        assert abs(k_default - k_explicit) < 1e-9

    def test_result_never_exceeds_one(self):
        # Even with extreme edge the Kelly fraction is capped by the formula
        k = _kelly(0.99, 0.01)
        assert k <= 1.0


class TestSizeRecommendation:
    def test_returns_none_below_ev_threshold(self):
        # EV = (0.52 - 0.50) / 0.50 = 0.04 — but let's find a case below threshold
        # EV_MIN_THRESHOLD = 0.03; yes_ev(0.515, 0.50) ≈ 0.03 which is borderline.
        # Use something clearly below: yes_ev(0.514, 0.50) ≈ 0.028 < 0.03
        assert size_recommendation(0.514, 0.50, "yes") is None

    def test_returns_dict_above_threshold(self):
        rec = size_recommendation(0.65, 0.50, "yes")
        assert rec is not None
        assert rec["ev"] >= EV_MIN_THRESHOLD
        assert "kelly_fraction" in rec
        assert "sizing_note" in rec

    def test_no_side_tag(self):
        rec = size_recommendation(0.35, 0.50, "no")
        assert rec is not None
        assert rec["ev_side"] == "NO"

    def test_yes_side_trade_price_equals_market_p(self):
        rec = size_recommendation(0.65, 0.50, "yes")
        assert rec is not None
        assert abs(rec["trade_price"] - 0.50) < 1e-9

    def test_no_side_trade_price_is_complement(self):
        rec = size_recommendation(0.35, 0.60, "no")
        assert rec is not None
        assert abs(rec["trade_price"] - round(1 - 0.60, 4)) < 1e-9

    def test_kelly_fraction_positive_when_ev_positive(self):
        rec = size_recommendation(0.70, 0.50, "yes")
        assert rec is not None
        assert rec["kelly_fraction"] > 0

    def test_estimated_q_and_market_p_recorded(self):
        rec = size_recommendation(0.65, 0.40, "yes")
        assert rec is not None
        assert abs(rec["estimated_q"] - 0.65) < 1e-4
        assert abs(rec["market_p"]    - 0.40) < 1e-4


# ── pre_trade_gate helpers ────────────────────────────────────────────────────

_NOW = datetime.now(timezone.utc)

# Module-level constants used when patching the gate with a live bankroll
_TEST_BANKROLL    = 1000.0
_MAX_PORTFOLIO    = Decimal("330.00")   # MAX_PORTFOLIO_PCT (0.33) × 1000
_MAX_POSITION     = Decimal("100.00")  # MAX_POSITION_PCT  (0.10) × 1000


def _signal(
    strategy="spread_engine",
    market_id="mkt1",
    signal_id=1,
    age_secs=0,
    signal_score=0.9,
    token_ids=None,
):
    if token_ids is None:
        token_ids = ["tok_yes", "tok_no"]
    return {
        "id":           signal_id,
        "strategy":     strategy,
        "market_id":    market_id,
        "signal_score": signal_score,
        "token_ids":    token_ids,
        "emitted_at":   _NOW - timedelta(seconds=age_secs),
        "metadata":     {},
    }


def _live_bankroll():
    """Patch context that sets a non-zero bankroll and the derived exposure caps."""
    return (
        patch.object(ptg, "BANKROLL_USDC",              _TEST_BANKROLL),
        patch.object(ptg, "_MAX_PORTFOLIO_EXPOSURE",    _MAX_PORTFOLIO),
        patch.object(ptg, "_MAX_EXPOSURE_PER_POSITION", _MAX_POSITION),
    )


# ── pre_trade_gate.check ──────────────────────────────────────────────────────

class TestPreTradeGateCheck:
    def test_unknown_strategy_rejected(self):
        ok, reason = ptg.check(_signal(strategy="ghost_engine"))
        assert not ok
        assert "allowlist" in reason

    def test_zero_bankroll_rejected(self):
        with patch.object(ptg, "BANKROLL_USDC", 0.0):
            ok, reason = ptg.check(_signal())
        assert not ok
        assert "BANKROLL_USDC" in reason

    def test_stale_signal_rejected(self):
        # MAX_SIGNAL_AGE_SECS = 60; 120s old is stale
        bl, mp, mx = _live_bankroll()
        with bl, mp, mx:
            ok, reason = ptg.check(_signal(age_secs=120))
        assert not ok
        assert "stale" in reason

    def test_fresh_signal_passes_staleness_gate(self):
        bl, mp, mx = _live_bankroll()
        with bl, mp, mx, \
             patch.object(ptg.db, "order_exists_for_signal", return_value=False), \
             patch.object(ptg.db, "get_total_open_exposure", return_value=0.0), \
             patch.object(ptg.db, "get_position",            return_value=None):
            ok, _ = ptg.check(_signal(age_secs=5))
        assert ok

    def test_already_ordered_signal_rejected(self):
        bl, mp, mx = _live_bankroll()
        with bl, mp, mx, \
             patch.object(ptg.db, "order_exists_for_signal", return_value=True), \
             patch.object(ptg.db, "get_total_open_exposure", return_value=0.0):
            ok, reason = ptg.check(_signal())
        assert not ok
        assert "order already exists" in reason

    def test_portfolio_cap_exceeded_rejected(self):
        # 500 USDC open > _MAX_PORTFOLIO_EXPOSURE (330 USDC)
        bl, mp, mx = _live_bankroll()
        with bl, mp, mx, \
             patch.object(ptg.db, "order_exists_for_signal", return_value=False), \
             patch.object(ptg.db, "get_total_open_exposure", return_value=500.0):
            ok, reason = ptg.check(_signal())
        assert not ok
        assert "portfolio exposure" in reason

    def test_portfolio_just_below_cap_passes(self):
        bl, mp, mx = _live_bankroll()
        with bl, mp, mx, \
             patch.object(ptg.db, "order_exists_for_signal", return_value=False), \
             patch.object(ptg.db, "get_total_open_exposure", return_value=329.99), \
             patch.object(ptg.db, "get_position",            return_value=None):
            ok, _ = ptg.check(_signal())
        assert ok

    def test_position_cap_exceeded_rejected(self):
        # total_bought + working_buy = 110 > _MAX_POSITION (100)
        position = {"total_bought": Decimal("90"), "working_buy": Decimal("20")}
        bl, mp, mx = _live_bankroll()
        with bl, mp, mx, \
             patch.object(ptg.db, "order_exists_for_signal", return_value=False), \
             patch.object(ptg.db, "get_total_open_exposure", return_value=0.0), \
             patch.object(ptg.db, "get_position",            return_value=position):
            ok, reason = ptg.check(_signal())
        assert not ok
        assert "position exposure" in reason

    def test_position_exactly_at_cap_rejected(self):
        position = {"total_bought": Decimal("100"), "working_buy": Decimal("0")}
        bl, mp, mx = _live_bankroll()
        with bl, mp, mx, \
             patch.object(ptg.db, "order_exists_for_signal", return_value=False), \
             patch.object(ptg.db, "get_total_open_exposure", return_value=0.0), \
             patch.object(ptg.db, "get_position",            return_value=position):
            ok, _ = ptg.check(_signal())
        assert not ok

    def test_all_gates_pass(self):
        bl, mp, mx = _live_bankroll()
        with bl, mp, mx, \
             patch.object(ptg.db, "order_exists_for_signal", return_value=False), \
             patch.object(ptg.db, "get_total_open_exposure", return_value=0.0), \
             patch.object(ptg.db, "get_position",            return_value=None):
            ok, reason = ptg.check(_signal())
        assert ok
        assert reason == ""

    def test_no_token_ids_skips_position_gate(self):
        # Gate 6 is skipped when signal has no token_ids — should still approve
        bl, mp, mx = _live_bankroll()
        with bl, mp, mx, \
             patch.object(ptg.db, "order_exists_for_signal", return_value=False), \
             patch.object(ptg.db, "get_total_open_exposure", return_value=0.0):
            ok, _ = ptg.check(_signal(token_ids=[]))
        assert ok

    def test_no_emitted_at_skips_staleness_gate(self):
        sig = _signal()
        sig["emitted_at"] = None
        bl, mp, mx = _live_bankroll()
        with bl, mp, mx, \
             patch.object(ptg.db, "order_exists_for_signal", return_value=False), \
             patch.object(ptg.db, "get_total_open_exposure", return_value=0.0), \
             patch.object(ptg.db, "get_position",            return_value=None):
            ok, _ = ptg.check(sig)
        assert ok


# ── pre_trade_gate.check_reprice ──────────────────────────────────────────────

class TestPreTradeGateCheckReprice:
    def test_zero_bankroll_rejected(self):
        with patch.object(ptg, "BANKROLL_USDC", 0.0):
            ok, reason = ptg.check_reprice(_signal())
        assert not ok
        assert "BANKROLL_USDC" in reason

    def test_portfolio_cap_exceeded_rejected(self):
        bl, mp, mx = _live_bankroll()
        with bl, mp, mx, \
             patch.object(ptg.db, "get_total_open_exposure", return_value=500.0):
            ok, reason = ptg.check_reprice(_signal())
        assert not ok
        assert "portfolio exposure" in reason

    def test_position_cap_exceeded_rejected(self):
        position = {"total_bought": Decimal("90"), "working_buy": Decimal("20")}
        bl, mp, mx = _live_bankroll()
        with bl, mp, mx, \
             patch.object(ptg.db, "get_total_open_exposure", return_value=0.0), \
             patch.object(ptg.db, "get_position",            return_value=position):
            ok, reason = ptg.check_reprice(_signal())
        assert not ok
        assert "position exposure" in reason

    def test_all_gates_pass(self):
        bl, mp, mx = _live_bankroll()
        with bl, mp, mx, \
             patch.object(ptg.db, "get_total_open_exposure", return_value=0.0), \
             patch.object(ptg.db, "get_position",            return_value=None):
            ok, reason = ptg.check_reprice(_signal())
        assert ok
        assert reason == ""
