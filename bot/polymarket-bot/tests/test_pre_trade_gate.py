"""
Tests for pre_trade_gate._check_resolution_sanity and _parse_title_deadline.

Regression focus: spread_engine placed live BUY-YES orders on markets whose
titles asserted an already-elapsed deadline ("...by May 3" entered on May 29).
The title was stale relative to a future end_date that passed the scanner, so
no end_date filter could catch it — only a title-deadline parse can. The gate
must (1) block any strategy when the title deadline has elapsed, and (2) block
spread_engine entries with too little runway to resolution, while exempting
tail_yield_engine (targets near-expiry) and neg_risk (held to resolution).
"""
import os
import sys
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from execution import pre_trade_gate as gate


def _now():
    return datetime.now(timezone.utc)


class TestParseTitleDeadline:
    def test_no_year_picks_nearest_occurrence(self):
        d = gate._parse_title_deadline("Will X happen by May 3")
        assert d is not None
        assert (d.month, d.day) == (5, 3)
        # nearest occurrence is within a year of now
        assert abs((d - _now()).days) <= 366

    def test_explicit_year_is_respected(self):
        d = gate._parse_title_deadline("Peace deal by May 31, 2026?")
        assert d == datetime(2026, 5, 31, 23, 59, 59, tzinfo=timezone.utc)

    def test_non_date_phrases_do_not_parse(self):
        assert gate._parse_title_deadline("Fed cuts rates by 50bps in June") is None
        assert gate._parse_title_deadline("Will it rain tomorrow?") is None
        assert gate._parse_title_deadline("") is None

    def test_uses_end_of_day(self):
        d = gate._parse_title_deadline("Resolves on January 1, 2030")
        assert (d.hour, d.minute, d.second) == (23, 59, 59)


class TestResolutionSanity:
    def _signal(self, question=None):
        return {"metadata": {"question": question} if question else {}}

    def test_elapsed_title_deadline_blocks_any_strategy(self):
        meta = {"question": "Event happens by January 1, 2020", "end_date": None}
        with patch.object(gate.db, "get_market_meta", return_value=meta):
            for strat in ("spread_engine", "tail_yield_engine", "neg_risk_overround"):
                ok, reason = gate._check_resolution_sanity(self._signal(), strat, "mkt1")
                assert ok is False, strat
                assert "elapsed" in reason

    def test_future_title_with_runway_passes(self):
        end = _now() + timedelta(hours=72)
        meta = {"question": "Event by December 31, 2099", "end_date": end}
        with patch.object(gate.db, "get_market_meta", return_value=meta):
            ok, reason = gate._check_resolution_sanity(self._signal(), "spread_engine", "mkt1")
            assert ok is True, reason

    def test_spread_blocked_with_insufficient_runway(self):
        end = _now() + timedelta(hours=2)  # < ENTRY_MIN_HOURS_TO_RESOLUTION (6)
        meta = {"question": "Event by December 31, 2099", "end_date": end}
        with patch.object(gate.db, "get_market_meta", return_value=meta):
            ok, reason = gate._check_resolution_sanity(self._signal(), "spread_engine", "mkt1")
            assert ok is False
            assert "runway" in reason

    def test_tail_yield_exempt_from_runway(self):
        end = _now() + timedelta(hours=2)  # near-expiry is intentional for tail_yield
        meta = {"question": "Event by December 31, 2099", "end_date": end}
        with patch.object(gate.db, "get_market_meta", return_value=meta):
            ok, reason = gate._check_resolution_sanity(self._signal(), "tail_yield_engine", "mkt1")
            assert ok is True, reason

    def test_naive_end_date_is_treated_as_utc(self):
        end = (_now() + timedelta(hours=2)).replace(tzinfo=None)  # naive
        meta = {"question": "Event by December 31, 2099", "end_date": end}
        with patch.object(gate.db, "get_market_meta", return_value=meta):
            ok, _ = gate._check_resolution_sanity(self._signal(), "spread_engine", "mkt1")
            assert ok is False  # still computes runway, doesn't raise

    def test_missing_market_meta_does_not_block(self):
        with patch.object(gate.db, "get_market_meta", return_value=None):
            ok, reason = gate._check_resolution_sanity(self._signal(), "spread_engine", "mkt1")
            assert ok is True, reason
