"""Native bot automation domain model and schedule computation.

Declarative action types only. No eval/exec/subprocess. The schedule is a
simple weekly/daily trigger stored in SQLite; JobQueue is the runtime
projection, not the source of truth.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from telepost.domain.hot import (
    active_timezone,
    parse_schedule_time,
    parse_weekday,
)

ACTION_HOT = "hot"
ACTION_LABELS = {ACTION_HOT: "🔥 热榜"}
TARGET_REVIEW_CHAT = "review_chat"


@dataclass
class AutomationTask:
    """One persisted automation rule."""
    id: Optional[int]
    name: str
    enabled: bool
    schedule_type: str  # "weekly" | "daily"
    schedule_day: int  # 0=Mon..6=Sun (weekly only)
    schedule_hour: int
    schedule_minute: int
    action_type: str
    action_payload: Dict[str, Any] = field(default_factory=dict)
    target_chat_id: str = ""
    timezone: str = ""
    created_by: int = 0
    created_at: float = 0.0
    updated_at: float = 0.0
    last_run_at: Optional[float] = None
    last_run_status: Optional[str] = None
    last_error: Optional[str] = None

    @property
    def time_label(self) -> str:
        return f"{self.schedule_hour:02d}:{self.schedule_minute:02d}"

    @property
    def day_label(self) -> str:
        if self.schedule_type == "daily":
            return "每天"
        names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
        return names[self.schedule_day] if 0 <= self.schedule_day <= 6 else "?"

    @property
    def schedule_label(self) -> str:
        return f"{self.day_label} {self.time_label}"

    @property
    def action_label(self) -> str:
        if self.action_type == ACTION_HOT:
            scope = self.action_payload.get("scope", "all")
            limit = self.action_payload.get("limit", 10)
            scope_text = "本周热榜" if scope == "week" else "全站热榜"
            return f"{scope_text} TOP {limit}"
        return self.action_type

    def next_run_at(self, now: Optional[datetime] = None) -> Optional[float]:
        """Compute the next occurrence timestamp after ``now`` (exclusive).

        Returns None for disabled tasks.
        """
        if not self.enabled:
            return None
        tz = active_timezone()
        now = now.astimezone(tz) if now else datetime.now(tz)
        candidate = now.replace(
            hour=self.schedule_hour, minute=self.schedule_minute,
            second=0, microsecond=0,
        )
        if candidate <= now:
            candidate += timedelta(days=1)
        if self.schedule_type == "weekly":
            days_ahead = (self.schedule_day - candidate.weekday()) % 7
            if days_ahead == 0 and candidate <= now:
                days_ahead = 7
            candidate += timedelta(days=days_ahead)
        return candidate.timestamp()

    def to_json(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "enabled": self.enabled,
            "schedule_type": self.schedule_type,
            "schedule_day": self.schedule_day,
            "schedule_hour": self.schedule_hour,
            "schedule_minute": self.schedule_minute,
            "action_type": self.action_type,
            "action_payload": self.action_payload,
            "target_chat_id": self.target_chat_id,
            "timezone": self.timezone,
            "created_by": self.created_by,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_run_at": self.last_run_at,
            "last_run_status": self.last_run_status,
            "last_error": self.last_error,
        }


class AutomationArgError(ValueError):
    """Raised when /schedule add arguments cannot be parsed."""


def parse_automation_add(args: list, *, created_by: int, target_chat_id: str) -> AutomationTask:
    """Parse ``/schedule add weekly-hot sunday 20:00 10`` into a task.

    The action type is the first arg; only ``weekly-hot`` is supported for now.
    No eval. Unknown input raises.
    """
    if len(args) < 4:
        raise AutomationArgError(
            "用法：/schedule add weekly-hot <星期> <HH:MM> <TOP N>\n"
            "例：/schedule add weekly-hot sunday 20:00 10"
        )
    action_raw = args[0].lower().strip()
    if action_raw != "weekly-hot":
        raise AutomationArgError(
            f"暂不支持任务类型「{args[0]}」。当前可用：weekly-hot"
        )
    day = parse_weekday(args[1])
    hour, minute = parse_schedule_time(args[2])
    if not args[3].isdigit():
        raise AutomationArgError(f"数量应为数字，收到：{args[3]}")
    limit = int(args[3])
    if limit < 1:
        raise AutomationArgError("数量至少为 1")
    from telepost.domain.hot import HOT_LIMIT_MAX
    limit = min(limit, HOT_LIMIT_MAX)
    tz_name = (active_timezone().key)
    return AutomationTask(
        id=None,
        name=f"每周热榜",
        enabled=True,
        schedule_type="weekly",
        schedule_day=day,
        schedule_hour=hour,
        schedule_minute=minute,
        action_type=ACTION_HOT,
        action_payload={"scope": "week", "limit": limit},
        target_chat_id=target_chat_id,
        timezone=tz_name,
        created_by=created_by,
    )


def parse_automation_payload(raw: str) -> Dict[str, Any]:
    """Deserialize action_payload from DB (safe JSON parse)."""
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}
