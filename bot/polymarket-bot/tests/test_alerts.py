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
