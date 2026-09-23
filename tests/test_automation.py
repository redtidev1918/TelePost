"""Tests for automation domain parsing, schedule computation, and storage."""
import time
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from telepost.domain.automation import (
    ACTION_HOT,
    AutomationArgError,
    AutomationTask,
    parse_automation_add,
    parse_automation_payload,
)
from telepost.domain.hot import HotQuery, active_timezone


class TestParseAutomationAdd:
    def test_valid(self):
        task = parse_automation_add(
            ["weekly-hot", "sunday", "20:00", "10"],
            created_by=12345,
            target_chat_id="-100123456",
        )
        assert task.action_type == ACTION_HOT
        assert task.schedule_type == "weekly"
        assert task.schedule_day == 6  # Sunday
        assert task.schedule_hour == 20
        assert task.schedule_minute == 0
        assert task.action_payload == {"scope": "week", "limit": 10}
        assert task.target_chat_id == "-100123456"

    def test_wrong_action(self):
        with pytest.raises(AutomationArgError):
            parse_automation_add(["eval", "sunday", "20:00", "10"], created_by=1, target_chat_id="")

    def test_bad_time(self):
        with pytest.raises(ValueError):
            parse_automation_add(["weekly-hot", "sunday", "25:99", "10"], created_by=1, target_chat_id="")

    def test_bad_limit(self):
        with pytest.raises(AutomationArgError):
            parse_automation_add(["weekly-hot", "sunday", "20:00", "abc"], created_by=1, target_chat_id="")

    def test_too_few_args(self):
        with pytest.raises(AutomationArgError):
            parse_automation_add(["weekly-hot"], created_by=1, target_chat_id="")

    def test_no_eval(self):
        """Declarative action types only — eval/exec/subprocess are never valid."""
        with pytest.raises(AutomationArgError):
            parse_automation_add(["__import__", "os", "system", "ls"], created_by=1, target_chat_id="")


class TestNextRun:
    def _make_task(self, day=6, hour=20, minute=0, enabled=True, stype="weekly"):
        return AutomationTask(
            id=1, name="test", enabled=enabled,
            schedule_type=stype, schedule_day=day,
            schedule_hour=hour, schedule_minute=minute,
            action_type=ACTION_HOT, action_payload={"scope": "week", "limit": 10},
            target_chat_id="-100123456",
        )

    def test_disabled_returns_none(self):
        assert self._make_task(enabled=False).next_run_at() is None

    def test_daily_next(self):
        tz = ZoneInfo("Asia/Shanghai")
        now = datetime(2026, 9, 23, 19, 0, 0, tzinfo=tz)
        task = self._make_task(stype="daily", hour=20)
        result = task.next_run_at(now)
        dt = datetime.fromtimestamp(result, tz=tz)
        assert dt.hour == 20
        assert dt.day == 23  # Same day 20:00

    def test_daily_past_time_tomorrow(self):
        tz = ZoneInfo("Asia/Shanghai")
        now = datetime(2026, 9, 23, 21, 0, 0, tzinfo=tz)
        task = self._make_task(stype="daily", hour=20)
        result = task.next_run_at(now)
        dt = datetime.fromtimestamp(result, tz=tz)
        assert dt.day == 24  # Tomorrow

    def test_weekly_sunday(self):
        tz = ZoneInfo("Asia/Shanghai")
        now = datetime(2026, 9, 23, 19, 0, 0, tzinfo=tz)  # Wed
        task = self._make_task(day=6, hour=20)  # Sunday 20:00
        result = task.next_run_at(now)
        dt = datetime.fromtimestamp(result, tz=tz)
        assert dt.weekday() == 6  # Sunday
        assert dt.hour == 20


class TestPayloadParse:
    def test_valid_json(self):
        assert parse_automation_payload('{"scope":"week","limit":10}') == {"scope": "week", "limit": 10}

    def test_invalid_json(self):
        assert parse_automation_payload("not json") == {}

    def test_empty(self):
        assert parse_automation_payload("") == {}


class TestHotQueryLabel:
    def test_scope_labels(self):
        assert HotQuery(scope="all", limit=10).scope_label == "全部时间"
        assert HotQuery(scope="week", limit=10).scope_label == "本周"
