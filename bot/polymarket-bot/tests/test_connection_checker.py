"""
Tests for connection_checker.check_geoblock — the startup geoblock probe.

Polymarket geoblocks order placement by egress IP region while leaving reads and
auth open, so this probe is the only thing that surfaces a trading block before
the first order 403s. It must persist its result, alert when blocked, and never
raise (a flaky probe can't be allowed to take down the executor).
"""
import json
import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from execution import connection_checker as cc  # noqa: E402  (path shim above)


def _fake_response(payload):
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = payload
    return resp


class TestCheckGeoblock:
    def test_blocked_region_alerts_and_persists(self):
        payload = {"blocked": True, "ip": "1.2.3.4", "country": "NL", "region": "NH"}
        with patch.object(cc.httpx, "get", return_value=_fake_response(payload)), \
             patch.object(cc.db, "set_config") as mock_set, \
             patch.object(cc.alerts, "geoblock_detected") as mock_alert:
            status = cc.check_geoblock()

        assert status["blocked"] is True
        assert status["country"] == "NL"
        mock_alert.assert_called_once()
        # persisted under the geoblock key as JSON
        key, value = mock_set.call_args[0]
        assert key == cc.GEOBLOCK_STATUS_KEY
        assert json.loads(value)["blocked"] is True

    def test_allowed_region_does_not_alert(self):
        payload = {"blocked": False, "ip": "41.160.224.178", "country": "ZA", "region": "GP"}
        with patch.object(cc.httpx, "get", return_value=_fake_response(payload)), \
             patch.object(cc.db, "set_config"), \
             patch.object(cc.alerts, "geoblock_detected") as mock_alert:
            status = cc.check_geoblock()

        assert status["blocked"] is False
        assert status["country"] == "ZA"
        mock_alert.assert_not_called()

    def test_probe_failure_is_non_fatal(self):
        with patch.object(cc.httpx, "get", side_effect=Exception("network down")), \
             patch.object(cc.db, "set_config") as mock_set, \
             patch.object(cc.alerts, "geoblock_detected") as mock_alert:
            status = cc.check_geoblock()   # must not raise

        assert status["blocked"] is None
        assert "network down" in status["error"]
        mock_alert.assert_not_called()      # unknown != blocked
        mock_set.assert_called_once()       # still records the (error) status
