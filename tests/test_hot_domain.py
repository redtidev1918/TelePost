"""Tests for /hot / /hotweek parser and week semantics."""
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from telepost.domain.hot import (
    HotArgError,
    HotQuery,
    parse_hot_query,
    week_start_utc,
    week_range_label,
    active_timezone,
)


class TestHotParser:
    def test_default(self):
        q = parse_hot_query([])
        assert q.scope == "all"
        assert q.limit == 10

    def test_limit(self):
        q = parse_hot_query(["20"])
        assert q.scope == "all"
        assert q.limit == 20

    def test_week_first(self):
        q = parse_hot_query(["week"])
        assert q.scope == "week"
        assert q.limit == 10

    def test_week_then_limit(self):
        q = parse_hot_query(["week", "10"])
        assert q.scope == "week"
        assert q.limit == 10

    def test_limit_then_week(self):
        q = parse_hot_query(["10", "week"])
        assert q.scope == "week"
        assert q.limit == 10

    def test_hotweek_alias(self):
        q = parse_hot_query([], week_alias=True)
        assert q.scope == "week"
        assert q.limit == 10

    def test_hotweek_alias_limit(self):
        q = parse_hot_query(["10"], week_alias=True)
        assert q.scope == "week"
        assert q.limit == 10

    def test_limit_clamped(self):
        q = parse_hot_query(["999999"])
        assert q.limit == 50

    def test_invalid_text(self):
        with pytest.raises(HotArgError):
            parse_hot_query(["nonsense"])

    def test_zero(self):
        with pytest.raises(HotArgError):
            parse_hot_query(["0"])

    def test_hotweek_invalid(self):
        with pytest.raises(HotArgError):
            parse_hot_query(["abc"], week_alias=True)

    def test_hotweek_extra(self):
        with pytest.raises(HotArgError):
            parse_hot_query(["10", "week"], week_alias=True)


class TestWeekSemantics:
    def test_week_start_is_monday_midnight(self):
        ts = week_start_utc()
        tz = active_timezone()
        dt = datetime.fromtimestamp(ts, tz=tz)
        assert dt.weekday() == 0
        assert dt.hour == 0
        assert dt.minute == 0
        assert dt.second == 0

    def test_week_start_sunday_night(self):
        # Pick a Sunday 23:00
        tz = ZoneInfo("Asia/Shanghai")
        sunday_night = datetime(2026, 9, 27, 23, 0, 0, tzinfo=tz)
        ts = week_start_utc(sunday_night)
        dt = datetime.fromtimestamp(ts, tz=tz)
        # Sunday's week start is the Monday 6 days ago
        assert dt.weekday() == 0
        assert dt.hour == 0
        # The Monday should be 2026-09-21
        assert dt.strftime("%Y-%m-%d") == "2026-09-21"

    def test_week_start_monday_morning(self):
        tz = ZoneInfo("Asia/Shanghai")
        monday_am = datetime(2026, 9, 28, 6, 0, 0, tzinfo=tz)
        ts = week_start_utc(monday_am)
        dt = datetime.fromtimestamp(ts, tz=tz)
        assert dt.strftime("%Y-%m-%d") == "2026-09-28"

    def test_week_range_label_not_empty(self):
        label = week_range_label()
        assert len(label) > 0
        assert "—" in label
