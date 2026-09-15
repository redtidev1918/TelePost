"""Unified submitter publication notification (§notify-submitter).

ONE pipeline for every submission path. The trigger is **publication success**
(``published`` channel confirm), never review approval:

* DIRECT_PUBLISH (chat):  published → notify
* REVIEW_REQUIRED (original): published → notify ("通过审核并发布")
* REVIEW_REQUIRED + editorial: published → notify + change summary

The enqueue stores a publication CONTEXT; the message text is formatted at
delivery time (so the policy/format can evolve without re-sending). One
idempotent row per publication message id — replay can never double-DM.
Service submissions (``submitter_user_id IS NULL``) are skipped: an API
credential/actor holder is never substituted for a submitter.
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from telepost.storage.sqlite.submitter_notifications import (
    SubmitterNotificationRepository,
)

logger = logging.getLogger(__name__)

# Unified policy: OFF / PUBLISHED_ONLY / PUBLISHED_WITH_EDITORIAL_SUMMARY.
POLICY_OFF = "off"
POLICY_PUBLISHED = "published"
POLICY_WITH_CHANGES = "with_changes"


@dataclass
class PublicationContext:
    """Everything the notification formatter needs; built by the application
    layer, never guessed by the Telegram adapter (§12)."""

    source: str                    # chat_direct | review | editorial | api_direct
    publication_id: int            # stable channel message id of the publish
    submitter_user_id: int
    submitter_username: str = ""
    anonymous: bool = False
    review_id: Optional[int] = None
    revision_id: Optional[int] = None
    change_summary: List[str] = field(default_factory=list)
    link: str = ""
    submission_kind: str = ""      # illustration|novel|… (display only)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class SubmitterNotifyService:
    def __init__(self, repository: Optional[SubmitterNotificationRepository] = None):
        self._repo = repository or SubmitterNotificationRepository()

    def policy_enabled(self) -> bool:
        from config.settings import SUBMITTER_PUBLISH_NOTIFY
        return str(SUBMITTER_PUBLISH_NOTIFY or "off").strip().lower() != POLICY_OFF

    def include_changes(self) -> bool:
        from config.settings import SUBMITTER_PUBLISH_NOTIFY
        return str(SUBMITTER_PUBLISH_NOTIFY or "off").strip().lower() == POLICY_WITH_CHANGES

    async def notify_published(self, context: PublicationContext) -> bool:
        """Publication-success hook. Returns True when a durable intent was
        enqueued (or already existed); never raises on enqueue failure."""
        if context.submitter_user_id is None or int(context.submitter_user_id) <= 0:
            return False  # service submission: no human submitter (§6)
        if not self.policy_enabled():
            return False
        # Anonymous humans keep internal ownership: they still receive the DM
        # (§5). Nothing about the public attribution appears in it.
        try:
            return await self._repo.enqueue(
                int(context.publication_id),
                int(context.submitter_user_id),
                context.to_dict(),
            )
        except Exception as exc:  # enqueue must never break the publication
            logger.warning("投稿者发布通知入队失败: message=%s error=%s",
                           context.publication_id, exc)
            return False


def format_publication_message(payload: Dict[str, Any], include_changes: bool,
                               editor_label: str = "频道主") -> str:
    """Single formatter for the three paths (§11): one place, no handler copies."""
    source = str(payload.get("source") or "publication")
    anonymous = bool(payload.get("anonymous"))

    if source in ("chat_direct", "api_direct"):
        head = "✅ 你的投稿已发布"
    elif source == "editorial":
        head = "✅ 你的投稿已发布"
        if include_changes:
            summary = [str(x) for x in (payload.get("change_summary") or []) if x]
            if summary:
                lines = [head, "", f"{editor_label}在发布前进行了以下编辑：", ""]
                lines += [f"• {item}" for item in summary[:12]]
                return "\n".join(lines)
    else:  # review (original, no edits)
        head = "✅ 你的投稿已通过审核并发布"

    lines = [head]
    if anonymous:
        lines.append("（匿名投稿）")
    link = str(payload.get("link") or "")
    if link:
        lines.append("")
        lines.append(f"🔗 查看发布内容：{link}")
    return "\n".join(lines)


async def flush_submitter_notifications(bot, *, limit: int = 20) -> int:
    """Periodic durable deliverer (§8/§9). Telegram failure retries the ROW;
    the publication stands regardless. Returns the count delivered."""
    from telepost.observability import audit

    service = SubmitterNotifyService()
    repo = service._repo  # noqa: SLF001
    rows = await repo.pending(limit=limit)
    if not rows:
        return 0
    include_changes = service.include_changes()
    sent = 0
    for row in rows:
        try:
            payload = json.loads(row["payload"] or "{}")
            text = format_publication_message(payload, include_changes)
            result = await bot.send_message(
                chat_id=int(row["telegram_user_id"]), text=text
            )
            if await repo.mark_sent(int(row["id"]), int(result.message_id)):
                sent += 1
            await audit.record_event(
                "submitter_notification.sent",
                review_id=row.get("review_id") or None,
                detail={"notification_id": row["id"], "message_id": result.message_id},
            )
        except Exception as exc:  # durable retry; never drop
            logger.warning("投稿者通知发送失败: id=%s error=%s", row["id"], exc)
            await repo.record_error(int(row["id"]), str(exc))
    return sent