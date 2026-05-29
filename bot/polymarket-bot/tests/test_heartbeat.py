"""
Unit tests for the executor heartbeat staleness helper (db._heartbeat_age_secs).
Pure/DB-free — guards the logic the scheduler watchdog uses to tell a hung
executor from a healthy one (2026-05-18 silent-hang incident).
"""
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import db


NOW = datetime(2026, 5, 29, 12, 0, 0, tzinfo=timezone.utc)


def test_missing_heartbeat_returns_none():
    assert db._heartbeat_age_secs(None, now=NOW) is None
    assert db._heartbeat_age_secs("", now=NOW) is None


def test_unparseable_returns_none():
    assert db._heartbeat_age_secs("not-a-timestamp", now=NOW) is None


def test_recent_heartbeat_small_age():
    iso = (NOW - timedelta(seconds=12)).isoformat()
    age = db._heartbeat_age_secs(iso, now=NOW)
    assert age == 12.0


def test_stale_heartbeat_large_age():
    iso = (NOW - timedelta(minutes=10)).isoformat()
    age = db._heartbeat_age_secs(iso, now=NOW)
    assert age == 600.0


def test_z_suffix_is_handled():
    iso = "2026-05-29T11:59:00Z"
    assert db._heartbeat_age_secs(iso, now=NOW) == 60.0


def test_naive_timestamp_treated_as_utc():
    iso = "2026-05-29T11:59:30"   # no tzinfo
    assert db._heartbeat_age_secs(iso, now=NOW) == 30.0
