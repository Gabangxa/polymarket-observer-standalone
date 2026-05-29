"""
Tests for alerts._send — the Discord webhook POST.

Regression focus: Discord's Cloudflare edge returns 403 Forbidden to the default
urllib User-Agent ("Python-urllib/x.y"), which silently drops EVERY alert. _send
must always send a custom User-Agent.
"""
import json
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import alerts


class TestSend:
    def setup_method(self):
        self._saved = alerts._WEBHOOK
        alerts._WEBHOOK = "https://discord.test/api/webhooks/abc/def"

    def teardown_method(self):
        alerts._WEBHOOK = self._saved

    def test_send_sets_non_default_user_agent(self):
        with patch("urllib.request.urlopen") as mock_open:
            alerts._send("hello")
        req = mock_open.call_args[0][0]
        ua = req.get_header("User-agent")
        assert ua, "User-Agent header must be set or Discord 403s the request"
        assert "python-urllib" not in ua.lower(), (
            "default urllib User-Agent is blocked by Discord's Cloudflare (403)"
        )

    def test_send_posts_json_content(self):
        with patch("urllib.request.urlopen") as mock_open:
            alerts._send("trade filled")
        req = mock_open.call_args[0][0]
        assert req.get_method() == "POST"
        assert json.loads(req.data.decode()) == {"content": "trade filled"}

    def test_send_noops_when_webhook_unset(self):
        alerts._WEBHOOK = ""
        with patch("urllib.request.urlopen") as mock_open:
            alerts._send("nobody listening")
        mock_open.assert_not_called()


class TestZeroSignalCooldown:
    """zero_signal_streak fires every pipeline run once past threshold; it must
    rate-limit per engine so it doesn't flood Discord every ~30s."""

    def setup_method(self):
        alerts._alert_cooldowns.clear()

    def teardown_method(self):
        alerts._alert_cooldowns.clear()

    def test_first_alert_sends_repeat_suppressed(self):
        with patch.object(alerts, "_send") as mock_send:
            alerts.zero_signal_streak("spread_engine", 6, None)
            alerts.zero_signal_streak("spread_engine", 7, None)
        assert mock_send.call_count == 1, "second within-cooldown alert must be suppressed"

    def test_cooldown_is_per_engine(self):
        with patch.object(alerts, "_send") as mock_send:
            alerts.zero_signal_streak("spread_engine", 6, None)
            alerts.zero_signal_streak("neg_risk_engine", 6, None)
        assert mock_send.call_count == 2, "different engines must alert independently"

    def test_alert_resends_after_cooldown_elapses(self):
        with patch.object(alerts, "_send") as mock_send:
            alerts.zero_signal_streak("spread_engine", 6, None)
            # age the recorded send past the cooldown window
            alerts._alert_cooldowns["zero_signal:spread_engine"] -= (
                alerts.ZERO_SIGNAL_ALERT_COOLDOWN_SECS + 1
            )
            alerts.zero_signal_streak("spread_engine", 99, None)
        assert mock_send.call_count == 2


class TestOrderSkipped:
    """Pre-CLOB failures (size-above-cap, missing price, etc.) produce no CLOB
    rejection, so order_skipped is the only Discord signal for a missed trade."""

    def setup_method(self):
        alerts._alert_cooldowns.clear()

    def teardown_method(self):
        alerts._alert_cooldowns.clear()

    def test_sends_with_reason_and_detail(self):
        with patch.object(alerts, "_send") as mock_send:
            alerts.order_skipped(
                strategy="spread_engine",
                market_id="0xmkt",
                question="Will X happen?",
                reason="size_above_cap",
                detail="position cap $2.50 < $4.85",
            )
        assert mock_send.call_count == 1
        body = mock_send.call_args[0][0]
        assert "size_above_cap" in body
        assert "position cap" in body

    def test_cooldown_is_per_reason(self):
        with patch.object(alerts, "_send") as mock_send:
            alerts.order_skipped("spread_engine", "m1", "q1", "size_above_cap")
            alerts.order_skipped("spread_engine", "m2", "q2", "size_above_cap")
            alerts.order_skipped("spread_engine", "m3", "q3", "missing_price_metadata")
        assert mock_send.call_count == 2, (
            "same reason within cooldown suppressed; a different reason still alerts"
        )
