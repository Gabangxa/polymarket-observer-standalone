"""
Tests for execution/order_manager.py critical paths.

Mocks the CLOB client and the db layer — no Postgres, no Polymarket. Covers:
  - _tick_dec caching + lookup failure fallback
  - _is_retryable classification (transient vs validation errors)
  - _backoff_retry behavior (success, retry, fail-fast on non-retryable)
  - _size_from_signal (Kelly path, cap fallback, below-min returns 0)
  - place_order spread_engine routing with and without yes_ask in metadata
  - place_order error paths (unknown strategy, missing token_id, missing price)
"""
import os
import sys
import types
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# Stub py_clob_client so order_manager's lazy import succeeds without the real package.
# This is acceptable for unit tests because we mock the CLOB client itself.
if "py_clob_client" not in sys.modules:
    _pcc      = types.ModuleType("py_clob_client")
    _pcc_ct   = types.ModuleType("py_clob_client.clob_types")

    class _OrderArgs:
        def __init__(self, token_id=None, price=None, size=None, side=None):
            self.token_id = token_id
            self.price = price
            self.size = size
            self.side = side
            self.expiration = None

    class _OrderType:
        GTC = "GTC"
        FOK = "FOK"
        GTD = "GTD"

    class _PartialCreateOrderOptions:
        def __init__(self, neg_risk=False):
            self.neg_risk = neg_risk

    _pcc_ct.OrderArgs                  = _OrderArgs
    _pcc_ct.OrderType                  = _OrderType
    _pcc_ct.PartialCreateOrderOptions  = _PartialCreateOrderOptions
    sys.modules["py_clob_client"]            = _pcc
    sys.modules["py_clob_client.clob_types"] = _pcc_ct

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from execution import order_manager as om


# ── _tick_dec ─────────────────────────────────────────────────────────────────

class TestTickDec:
    def setup_method(self):
        om._TICK_CACHE.clear()

    def test_first_call_queries_client(self):
        client = MagicMock()
        client.get_tick_size.return_value = "0.01"
        result = om._tick_dec(client, "tok_a")
        assert result == Decimal("0.01")
        client.get_tick_size.assert_called_once_with("tok_a")

    def test_second_call_uses_cache(self):
        client = MagicMock()
        client.get_tick_size.return_value = "0.001"
        om._tick_dec(client, "tok_b")
        om._tick_dec(client, "tok_b")
        om._tick_dec(client, "tok_b")
        assert client.get_tick_size.call_count == 1

    def test_lookup_failure_returns_default(self):
        client = MagicMock()
        client.get_tick_size.side_effect = Exception("timeout")
        result = om._tick_dec(client, "tok_c")
        assert result == om._TICK_DEFAULT

    def test_lookup_failure_does_not_cache(self):
        client = MagicMock()
        client.get_tick_size.side_effect = [Exception("timeout"), "0.001"]
        first = om._tick_dec(client, "tok_d")
        second = om._tick_dec(client, "tok_d")
        # First call defaults to 0.01, second call should retry and get 0.001
        assert first == Decimal("0.01")
        assert second == Decimal("0.001")


# ── _is_retryable ─────────────────────────────────────────────────────────────

class TestIsRetryable:
    @pytest.mark.parametrize("msg", [
        "Invalid order inputs",
        "Invalid Price for tick",
        "tick size violation",
        "min size 1 USDC",
        "insufficient balance",
        "Bad Request: signature malformed",
        "nonce already used",
        "order already exists",
        "order has expired",
    ])
    def test_validation_errors_are_not_retryable(self, msg):
        assert om._is_retryable(Exception(msg)) is False

    @pytest.mark.parametrize("msg", [
        "connection reset",
        "request timeout",
        "HTTP 503 Service Unavailable",
        "Internal Server Error",
        "EOF reading response",
    ])
    def test_transient_errors_are_retryable(self, msg):
        assert om._is_retryable(Exception(msg)) is True

    def test_match_is_case_insensitive(self):
        assert om._is_retryable(Exception("INVALID ORDER")) is False
        assert om._is_retryable(Exception("TICK SIZE")) is False


# ── _backoff_retry ────────────────────────────────────────────────────────────

class TestBackoffRetry:
    def test_returns_on_first_success(self):
        fn = MagicMock(return_value="ok")
        assert om._backoff_retry(fn, max_retries=3) == "ok"
        assert fn.call_count == 1

    def test_retries_transient_then_succeeds(self):
        attempts = [Exception("timeout"), Exception("503"), "ok"]
        fn = MagicMock(side_effect=attempts)
        with patch.object(om.time, "sleep") as sleeper:
            assert om._backoff_retry(fn, max_retries=5) == "ok"
        assert fn.call_count == 3
        # First two failures slept; third succeeded with no sleep
        assert sleeper.call_count == 2

    def test_fails_fast_on_validation_error(self):
        fn = MagicMock(side_effect=Exception("Invalid order inputs"))
        with patch.object(om.time, "sleep") as sleeper:
            with pytest.raises(Exception, match="Invalid order"):
                om._backoff_retry(fn, max_retries=5)
        assert fn.call_count == 1
        sleeper.assert_not_called()

    def test_exhausts_retries_on_persistent_transient(self):
        fn = MagicMock(side_effect=Exception("timeout"))
        with patch.object(om.time, "sleep"):
            with pytest.raises(Exception, match="timeout"):
                om._backoff_retry(fn, max_retries=3)
        assert fn.call_count == 3


# ── _size_from_signal ─────────────────────────────────────────────────────────

class TestSizeFromSignal:
    """_size_from_signal does `import db as _db` locally; patch the db module directly."""

    def test_kelly_fraction_capped_at_position_pct(self):
        # bankroll=1000, MAX_POSITION_PCT=0.10 → cap=100. Kelly=0.5 → 500 → capped at 100.
        signal = {"metadata": {"kelly_fraction": 0.5}}
        with patch("db.get_bankroll", return_value=1000.0), \
             patch("config.MAX_POSITION_PCT", 0.10):
            size = om._size_from_signal(signal, "BUY")
        assert size == Decimal("100.00")

    def test_kelly_below_cap_uses_kelly(self):
        signal = {"metadata": {"kelly_fraction": 0.05}}
        with patch("db.get_bankroll", return_value=1000.0), \
             patch("config.MAX_POSITION_PCT", 0.10):
            size = om._size_from_signal(signal, "BUY")
        assert size == Decimal("50.00")

    def test_no_kelly_uses_full_cap(self):
        signal = {"metadata": {}}
        with patch("db.get_bankroll", return_value=1000.0), \
             patch("config.MAX_POSITION_PCT", 0.10):
            size = om._size_from_signal(signal, "BUY")
        assert size == Decimal("100.00")

    def test_below_min_returns_zero(self):
        # bankroll=10, cap=1 → below _MIN_ORDER_USDC=5.0 → 0
        signal = {"metadata": {}}
        with patch("db.get_bankroll", return_value=10.0), \
             patch("config.MAX_POSITION_PCT", 0.10):
            size = om._size_from_signal(signal, "BUY")
        assert size == Decimal("0")


# ── place_order routing/error paths ───────────────────────────────────────────

def _base_signal(strategy="spread_engine", with_yes_ask=False, yes_price=0.45,
                 yes_ask=None, token_ids=None):
    md = {"yes_price": yes_price, "kelly_fraction": None, "question": "Q"}
    if with_yes_ask:
        md["yes_ask"] = yes_ask if yes_ask is not None else (yes_price + 0.02)
    return {
        "id":         101,
        "strategy":   strategy,
        "market_id":  "mkt_test",
        "token_ids":  token_ids if token_ids is not None else ["tok_yes", "tok_no"],
        "metadata":   md,
    }


class TestPlaceOrderRouting:
    def setup_method(self):
        om._TICK_CACHE.clear()

    def _patches(self, bankroll: float = 1000.0):
        """Stack of patches required for place_order() to run without a real DB or live network."""
        return [
            patch("db.get_bankroll",   return_value=bankroll),
            patch.object(om, "db"),
            patch.object(om, "alerts"),
            patch.object(om, "nats_bus"),
            patch("config.MAX_POSITION_PCT", 0.10),
        ]

    @staticmethod
    def _enter(stack):
        return [p.start() for p in stack]

    @staticmethod
    def _exit(stack):
        for p in stack:
            p.stop()

    def test_unknown_strategy_rejected(self):
        client = MagicMock()
        client.get_tick_size.return_value = "0.01"
        signal = _base_signal(strategy="ghost_engine")
        stack = self._patches()
        _, mock_db, _, _, _ = self._enter(stack)
        mock_db.insert_order.return_value = 1
        try:
            result = om.place_order(signal, client)
        finally:
            self._exit(stack)
        assert result["ok"] is False
        assert "no order logic" in result["error"]

    def test_missing_token_id_rejected(self):
        client = MagicMock()
        signal = _base_signal(token_ids=[])
        result = om.place_order(signal, client)
        assert result["ok"] is False
        assert "token_id" in result["error"]

    def test_spread_engine_missing_yes_ask_falls_back_to_midpoint(self):
        """yes_ask missing → quantize yes_price (midpoint) at tick. Order submits successfully."""
        client = MagicMock()
        client.get_tick_size.return_value = "0.01"
        client.create_order.return_value = "signed_blob"
        client.post_order.return_value = {"orderID": "exch_42"}

        signal = _base_signal(with_yes_ask=False, yes_price=0.45)
        stack = self._patches()
        _, mock_db, _, _, _ = self._enter(stack)
        mock_db.insert_order.return_value = 1
        try:
            result = om.place_order(signal, client)
        finally:
            self._exit(stack)

        assert result["ok"] is True
        oa = client.create_order.call_args[0][0]
        assert float(oa.price) == 0.45
        assert oa.side == "BUY"

    def test_spread_engine_with_yes_ask_posts_one_tick_below_ask(self):
        client = MagicMock()
        client.get_tick_size.return_value = "0.01"
        client.create_order.return_value = "signed_blob"
        client.post_order.return_value = {"orderID": "exch_99"}

        signal = _base_signal(with_yes_ask=True, yes_price=0.45, yes_ask=0.48)
        stack = self._patches()
        _, mock_db, _, _, _ = self._enter(stack)
        mock_db.insert_order.return_value = 1
        try:
            result = om.place_order(signal, client)
        finally:
            self._exit(stack)

        assert result["ok"] is True
        oa = client.create_order.call_args[0][0]
        # ask=0.48 quantized at 0.01 → 0.48; minus 1 tick → 0.47
        assert float(oa.price) == 0.47

    def test_spread_engine_yes_ask_at_one_tick_rejected(self):
        """If yes_ask quantizes to one tick, ask − tick is non-positive → reject cleanly."""
        client = MagicMock()
        client.get_tick_size.return_value = "0.01"
        signal = _base_signal(with_yes_ask=True, yes_ask=0.01)
        stack = self._patches()
        self._enter(stack)
        try:
            result = om.place_order(signal, client)
        finally:
            self._exit(stack)
        assert result["ok"] is False
        assert "non-positive" in result["error"]

    def test_validation_error_marks_order_rejected_without_retry(self):
        client = MagicMock()
        client.get_tick_size.return_value = "0.01"
        client.create_order.return_value = "signed_blob"
        client.post_order.side_effect = Exception("Invalid order inputs")

        signal = _base_signal(with_yes_ask=True, yes_ask=0.48)
        stack = self._patches()
        _, mock_db, _, _, _ = self._enter(stack)
        mock_db.insert_order.return_value = 1
        sleeper_patch = patch.object(om.time, "sleep")
        sleeper = sleeper_patch.start()
        try:
            result = om.place_order(signal, client)
        finally:
            sleeper_patch.stop()
            self._exit(stack)

        assert result["ok"] is False
        assert "Invalid order" in result["error"]
        sleeper.assert_not_called()
        assert client.post_order.call_count == 1
        rejected_calls = [c for c in mock_db.update_order_status.call_args_list
                          if c.args[1] == "REJECTED"]
        assert len(rejected_calls) == 1
