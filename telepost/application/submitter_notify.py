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


async def lookup_preview_url(publication_key: str) -> str:
    """Return the durable Telegraph preview URL of a publication, if one was
    recorded as a success. Used to attach the optional ``📖 在线阅读`` line to
    the submission success DM (§publication-presentation). Returns "" when the
    publication has no successful preview, so a broken link is never produced.
    """
    if not str(publication_key or "").strip():
        return ""
    try:
        from telepost.domain.novel_preview import PreviewStatus
        from telepost.storage.sqlite.novel_preview import PublicationPreviewRepository
        rec = await PublicationPreviewRepository().find(str(publication_key).strip())
        if rec is not None and rec.status == PreviewStatus.SUCCEEDED.value and rec.url:
            return rec.url
    except Exception as exc:  # never let a preview lookup break the notification
        logger.warning("preview url lookup failed for publication key: %s", exc)
    return ""

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
    preview_url: str = ""           # optional Telegraph "read online" enrichment URL
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


@dataclass
class ManagerAcceptanceContext:
    logical_submission_id: str
    submitter_user_id: int
    submitter_username: str = ""
    submitter_display_name: str = ""
    anonymous: bool = False
    review_id: Optional[int] = None
    publication_id: Optional[int] = None
    link: str = ""
    source: str = ""
    review_chat_id: str = ""
    control_message_id: Optional[int] = None
    status: str = "pending"
    actor_kind: str = "user"
    actor_subject: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class ManagerNotifyService:
    """Durable manager alert emitted once per logical human submission."""

    def __init__(self, repository: Optional[SubmitterNotificationRepository] = None):
        self._repo = repository or SubmitterNotificationRepository()

    async def notify_accepted(self, context: ManagerAcceptanceContext) -> bool:
        from config.settings import NOTIFY_OWNER, OWNER_ID

        if not NOTIFY_OWNER or not OWNER_ID:
            return False
        # A service/API submission has no human submitter; the API actor
        # identity still warrants an operator DM (§admin-plane).
        has_actor = bool(context.submitter_user_id) or bool(
            context.actor_subject and context.actor_subject != "user:0"
        )
        if not has_actor:
            return False
        if context.submitter_user_id and int(context.submitter_user_id) == int(OWNER_ID):
            return False
        try:
            return await self._repo.enqueue_manager(
                context.logical_submission_id, int(OWNER_ID), context.to_dict()
            )
        except Exception as exc:
            logger.warning("管理者新投稿通知入队失败: submission=%s error=%s",
                           context.logical_submission_id, exc)
            return False


def format_manager_acceptance(payload: Dict[str, Any]) -> tuple[str, Optional[dict]]:
    """Return plain text plus one intentional explicit-user link entity spec.

    Every source (chat / api / miniapp) is represented by its submitter or its
    API actor identity, always with a status and review/publication links when
    they exist. Missing links are shown explicitly rather than silently dropped.
    """
    anonymous = bool(payload.get("anonymous"))
    review_id = payload.get("review_id")
    link = str(payload.get("link") or "")
    source = str(payload.get("source") or "")
    submitter_uid = payload.get("submitter_user_id") or 0
    actor_subject = str(payload.get("actor_subject") or "")
    status = str(payload.get("status") or "pending")
    statuses = {
        "pending": "待审核",
        "pending_review": "待审核",
        "published": "已发布",
        "accepted": "已受理",
        "failed": "失败",
    }
    head = "📝 投稿通知"
    lines = [head]
    if source:
        label = "Telegram 私聊" if source in ("chat", "chat_direct") else (
            "Mini App" if "miniapp" in source else "HTTP API"
        )
        lines.append(f"来源：{label}")
    entity = None
    if not anonymous and submitter_uid:
        uid = int(submitter_uid)
        username = str(payload.get("submitter_username") or "").strip().lstrip("@")
        display_name = str(payload.get("submitter_display_name") or "").strip()
        label = f"@{username}" if username else (display_name or "Telegram 用户")
        lines += ["", f"投稿人：{label}"]
        start = len("\n".join(lines)) - len(label)
        entity = {
            "offset": start, "length": len(label), "url": f"tg://user?id={uid}",
        }
    elif not anonymous and actor_subject.startswith("api:"):
        token_id = actor_subject.split(":", 1)[1].strip()
        lines.append(f"API token：#{token_id}")
    elif anonymous:
        lines.append("（匿名投稿）")
    lines.append(f"状态：{statuses.get(status, status)}")
    if review_id:
        lines.append(f"审核稿：#{int(review_id)}")
    review_chat = str(payload.get("review_chat_id") or "").replace("@", "")
    control_msg = payload.get("control_message_id")
    if review_chat and control_msg:
        lines.append(f"🔗 审核：https://t.me/c/{review_chat.replace('-100', '')}/{int(control_msg)}")
    elif review_id:
        lines.append("🔗 审核：链接不可用")
    if link:
        lines += ["", f"📂 原投稿：{link}"]
    return "\n".join(lines), entity


def format_publication_message(payload: Dict[str, Any], include_changes: bool,
                               editor_label: str = "频道主") -> str:
    """Single formatter for the three paths (§11): one place, no handler copies."""
    source = str(payload.get("source") or "publication")
    anonymous = bool(payload.get("anonymous"))

    if source in ("chat_direct", "api_direct", "miniapp_direct"):
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
    # §publication-presentation: the Telegraph "read online" URL is an optional
    # Publication enrichment. It appears in the success DM ONLY when a real
    # preview exists; a failed/absent preview never shows a broken entry.
    preview_url = str(payload.get("preview_url") or "")
    if preview_url:
        lines.append(f"📖 在线阅读：{preview_url}")
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


async def flush_manager_notifications(bot, *, limit: int = 20) -> int:
    """Deliver durable manager alerts; only this intentional context links a user."""
    repo = SubmitterNotificationRepository()
    rows = await repo.pending_manager(limit=limit)
    sent = 0
    for row in rows:
        try:
            payload = json.loads(row["payload"] or "{}")
            text, entity = format_manager_acceptance(payload)
            kwargs = {"chat_id": int(row["telegram_user_id"]), "text": text}
            if entity:
                from telegram import MessageEntity
                entities = [MessageEntity(
                    type=MessageEntity.TEXT_LINK,
                    offset=entity["offset"], length=entity["length"],
                    url=entity["url"],
                )]
                kwargs["entities"] = MessageEntity.adjust_message_entities_to_utf_16(
                    text, entities
                )
            # §moderation: governance buttons ride the admin DM itself, so a
            # chat_direct post (no review card) is still actionable. Button
            # backends are actor-identity based, never review-scoped.
            from telegram import InlineKeyboardButton
            buttons = []
            if payload.get("submitter_user_id"):
                buttons.append(InlineKeyboardButton(
                    "🚫 封禁用户",
                    callback_data=f"admin_block:user:{int(payload['submitter_user_id'])}",
                ))
            actor = str(payload.get("actor_subject") or "")
            if actor.startswith("api:"):
                buttons.append(InlineKeyboardButton(
                    "🔑 禁用API",
                    callback_data=f"admin_block:api:{actor.split(':', 1)[1].strip()}",
                ))
            if buttons:
                from telegram import InlineKeyboardMarkup
                kwargs["reply_markup"] = InlineKeyboardMarkup([buttons])
            result = await bot.send_message(**kwargs)
            if await repo.mark_sent(int(row["id"]), int(result.message_id)):
                sent += 1
        except Exception as exc:
            logger.warning("管理者新投稿通知发送失败: id=%s error=%s", row["id"], exc)
            await repo.record_error(int(row["id"]), str(exc))
    return sent
