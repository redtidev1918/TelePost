"""Hot ranking query parsing and week semantics.

SSOT for /hot / /hotweek argument parsing. The heat score itself stays in
``utils/heat_calculator.py``; this module only decides scope and limit.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

HOT_LIMIT_MAX = 50
HOT_LIMIT_DEFAULT = 10

WEEKDAY_ALIASES = {
    "mon": 0, "monday": 0, "一": 0, "周一": 0, "星期一": 0,
    "tue": 1, "tuesday": 1, "二": 1, "周二": 1, "星期二": 1,
    "wed": 2, "wednesday": 2, "三": 2, "周三": 2, "星期三": 2,
    "thu": 3, "thursday": 3, "四": 3, "周四": 3, "星期四": 3,
    "fri": 4, "friday": 4, "五": 4, "周五": 4, "星期五": 4,
    "sat": 5, "saturday": 5, "六": 5, "周六": 5, "星期六": 5,
    "sun": 6, "sunday": 6, "日": 6, "周日": 6, "星期日": 6, "天": 6, "周日天": 6,
}

SCOPE_ALIASES = {
    "all": "all",
    "week": "week",
    "本周": "week",
    "全部": "all",
}


@dataclass(frozen=True)
class HotQuery:
    """Normalized hot ranking query."""
    scope: str  # "all" | "week"
    limit: int  # 1..HOT_LIMIT_MAX

    @property
    def scope_label(self) -> str:
        return "本周" if self.scope == "week" else "全部时间"


class HotArgError(ValueError):
    """Raised when /hot / /hotweek arguments cannot be parsed."""


def active_timezone() -> ZoneInfo:
    """Return the bot's configured timezone (TZ env, default Asia/Shanghai)."""
    name = (os.getenv("TZ", "Asia/Shanghai") or "Asia/Shanghai").strip() or "Asia/Shanghai"
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, KeyError, ValueError):
        return ZoneInfo("Asia/Shanghai")


def week_start_utc(now: Optional[datetime] = None) -> float:
    """Return the UTC timestamp of the current natural week start (Mon 00:00 local).

    Uses the bot's TZ. This is the definition of "本周" for /hotweek — not a
    rolling 7-day window.
    """
    tz = active_timezone()
    now = now.astimezone(tz) if now else datetime.now(tz)
    monday = now - timedelta(days=now.weekday())
    week_monday = monday.replace(hour=0, minute=0, second=0, microsecond=0)
    return week_monday.timestamp()


def week_range_label(now: Optional[datetime] = None) -> str:
    """Human-readable week range for the hot list header."""
    tz = active_timezone()
    now = now.astimezone(tz) if now else datetime.now(tz)
    monday = now - timedelta(days=now.weekday())
    week_monday = monday.replace(hour=0, minute=0, second=0, microsecond=0)
    return f"{week_monday.strftime('%m月%d日')} — {now.strftime('%m月%d日 %H:%M')}"


def parse_hot_query(args: list, *, week_alias: bool = False) -> HotQuery:
    """Parse /hot / /hotweek arguments into a normalized HotQuery.

    Accepted forms:
      /hot          → all, default limit
      /hot 20       → all, limit 20
      /hot week     → week, default limit
      /hot 10 week  → week, limit 10 (legacy order)
      /hot week 10  → week, limit 10
      /hotweek      → week, default limit
      /hotweek 10   → week, limit 10

    Raises HotArgError for anything else.
    """
    scope = "week" if week_alias else "all"
    limit = HOT_LIMIT_DEFAULT

    if week_alias and args:
        if len(args) == 1 and args[0].isdigit():
            limit = int(args[0])
            if limit < 1:
                raise HotArgError("数量至少为 1")
            limit = min(limit, HOT_LIMIT_MAX)
        else:
            raise HotArgError(f"/hotweek [数量] · 数量为 1-{HOT_LIMIT_MAX}")
        return HotQuery(scope=scope, limit=limit)

    for arg in args:
        lowered = str(arg).lower().strip()
        if lowered in SCOPE_ALIASES:
            scope = SCOPE_ALIASES[lowered]
        elif lowered.isdigit():
            val = int(lowered)
            if val < 1:
                raise HotArgError("数量至少为 1")
            limit = min(val, HOT_LIMIT_MAX)
        else:
            raise HotArgError(
                f"看不懂「{arg}」。用法：/hot [数量] [week] · 如 /hot 20 week"
            )
    return HotQuery(scope=scope, limit=limit)


def parse_weekday(value: str) -> int:
    """Parse a weekday string to Python weekday int (0=Mon..6=Sun)."""
    lowered = value.lower().strip()
    if lowered in WEEKDAY_ALIASES:
        return WEEKDAY_ALIASES[lowered]
    raise ValueError(f"无效星期：{value}")


def parse_schedule_time(value: str) -> tuple:
    """Parse HH:MM into (hour, minute). Raises ValueError on bad format."""
    parts = value.strip().split(":")
    if len(parts) != 2:
        raise ValueError(f"时间格式应为 HH:MM，收到：{value}")
    hour, minute = int(parts[0]), int(parts[1])
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"时间超出范围：{value}")
    return hour, minute
