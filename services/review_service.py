"""Headless review business logic shared by Telegram, HTTP and MCP.

The Telegram adapter stages previews and owns callbacks. This service owns the
review state transitions, publishing claim/idempotency and DTO/media access.

Persistence is delegated to
:class:`telepost.storage.sqlite.reviews.ReviewRepository`, whose conditional
UPDATEs are the single writer of ``pending_reviews.status``: two administrators
approving concurrently can never both publish, and a stale ``publishing`` row
(a process crashed mid-publish) can be reclaimed after PUBLISHING_STALE_SECONDS.
Terminal updates carry a status guard so a late writer can never clobber a row
that a stale reclaim already moved again.
"""

from __future__ import annotations

import io
import json
import logging
import os
import re
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional

from telepost.storage.sqlite.reviews import ReviewRepository

logger = logging.getLogger(__name__)

PUBLISHING_STALE_SECONDS = max(
    60.0, float(os.getenv("PUBLISHING_STALE_SECONDS", "300"))
)
MAX_REJECT_REASON_CHARS = 300
MEDIA_DOWNLOAD_MAX_BYTES = 20 * 1024 * 1024
PREVIEW_LONG_EDGE = 1600
THUMBNAIL_LONG_EDGE = 400


class ReviewError(Exception):
    code = "review_error"
    http_status = 409

    def __init__(self, message: str, *, details: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}


class ReviewNotFoundError(ReviewError):
    code = "review_not_found"
    http_status = 404


class ReviewStateError(ReviewError):
    code = "review_already_resolved"
    http_status = 409


class ReviewBusyError(ReviewStateError):
    code = "review_busy"
    http_status = 409


class MediaNotFoundError(ReviewError):
    code = "media_not_found"
    http_status = 404


class PreviewUnavailableError(ReviewError):
    code = "preview_unavailable"
    http_status = 409


class PublishFailedError(ReviewError):
    code = "publish_failed"
    http_status = 502

    def __init__(self, message: str, *, retry_hint: str = "", original: Optional[BaseException] = None):
        super().__init__(message)
        self.retry_hint = retry_hint
        self.original = original
        self.details = {}


@dataclass(frozen=True)
class ReviewMedia:
    index: int
    kind: str
    file_id: Optional[str]
    filename: Optional[str] = None
    mime_type: Optional[str] = None
    thumbnail_file_id: Optional[str] = None


@dataclass(frozen=True)
class ReviewItem:
    id: int
    status: str
    title: str
    note: str
    tags: List[str]
    link: str
    anonymous: bool
    spoiler: bool
    submitter_name: Optional[str]
    submitter_id: Optional[int]
    source_label: Optional[str]
    source_ref: Optional[str]
    scheduled_at: Optional[str]
    source: str
    target_id: str
    media: List[ReviewMedia]
    created_at: str
    updated_at: str
    error: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ReviewSummary:
    review_id: int
    title: str
    tags: List[str]
    media_count: int
    document_count: int
    spoiler: bool
    source_label: Optional[str]
    created_at: str
    status: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ActionResult:
    review_id: int
    status: str
    reused: bool
    message_id: Optional[int] = None
    link: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MediaResult:
    review_id: int
    index: int
    kind: str
    variant: str
    mime_type: str
    filename: Optional[str]
    size: int
    data: bytes
    original_kind: str

    def to_metadata(self) -> Dict[str, Any]:
        return {
            "review_id": self.review_id,
            "index": self.index,
            "kind": self.kind,
            "original_kind": self.original_kind,
            "variant": self.variant,
            "mime_type": self.mime_type,
            "filename": self.filename,
            "size": self.size,
        }


Publisher = Callable[..., Awaitable[Dict[str, Any]]]


def _iso(value: Optional[float]) -> Optional[str]:
    if not value:
        return None
    return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat()


def _tags(value: str) -> List[str]:
    return [tag for tag in (value or "").split() if tag]


def _media(row) -> List[ReviewMedia]:
    items: List[ReviewMedia] = []
    try:
        media = json.loads(row["media_json"] or "[]")
    except json.JSONDecodeError:
        media = []
    try:
        documents = json.loads(row["documents_json"] or "[]")
    except json.JSONDecodeError:
        documents = []

    index = 0
    for item in media:
        items.append(ReviewMedia(
            index=index,
            kind=str(item.get("type") or "media"),
            file_id=item.get("file_id"),
            thumbnail_file_id=item.get("thumbnail_file_id"),
        ))
        index += 1
    for item in documents:
        filename = item.get("filename") or "file"
        items.append(ReviewMedia(
            index=index,
            kind="document",
            file_id=item.get("file_id"),
            filename=filename,
        ))
        index += 1
    return items


def _to_item(row) -> ReviewItem:
    return ReviewItem(
        id=row["id"],
        status=row["status"],
        title=row["title"] or "",
        note=row["note"] or "",
        tags=_tags(row["tags"] or ""),
        link=row["link"] or "",
        anonymous=bool(row["anonymous"]),
        spoiler=bool(row["spoiler"]),
        submitter_name=row["username"] or None,
        submitter_id=row["user_id"],
        source_label=row["source_label"] or None,
        source_ref=row["source_ref"] or None,
        scheduled_at=row["scheduled_at"] or None,
        source=row["source"] or "api",
        target_id=row["target_id"] or "",
        media=_media(row),
        created_at=_iso(row["created_at"]) or "",
        updated_at=_iso(row["updated_at"]) or "",
        error=row["error"] or "",
    )


def clean_reason(value: Optional[str]) -> str:
    """Bound reject text and strip Telegram/control characters; audit-only."""
    if not value:
        return ""
    text = str(value).replace("\r", " ").replace("\n", " ")
    text = "".join(ch for ch in text if ch == " " or ch.isprintable())
    return text.strip()[:MAX_REJECT_REASON_CHARS]


def _audit(action: str, review_id: int, actor: Any, result: str, **extra) -> None:
    source = str(extra.pop("source", "service")).lower()
    payload = {
        "event": "review_action",
        "source": "mcp" if source == "mcp" else source,
        "review_id": review_id,
        "action": action,
        "actor": actor,
        "result": result,
    }
    payload.update({k: v for k, v in extra.items() if v is not None})
    logger.info("review audit %s", payload)


class ReviewService:
    def __init__(self, publisher: Optional[Publisher] = None,
                 repo: Optional[ReviewRepository] = None):
        self._publisher = publisher
        self._repo = repo or ReviewRepository()

    async def _publish(self, bot, row, spoiler: bool) -> Dict[str, Any]:
        publisher = self._publisher
        if publisher is None:
            from handlers.publish import publish_from_file_ids
            publisher = publish_from_file_ids
        kwargs = dict(
            tags=row["tags"],
            title=row["title"],
            note=row["note"],
            link=row["link"],
            anonymous=bool(row["anonymous"]),
            spoiler=spoiler,
            user_id=row["user_id"],
            username=row["username"],
        )
        # Carry durable dedupe identity so an approved review participates in
        # the same idempotency/ledger guarantees as direct API publication.
        if row["idempotency_key"]:
            kwargs["idempotency_key"] = f"review:{row['id']}:{row['idempotency_key']}"
        for column in ("target_id", "work_type", "pixiv_id"):
            try:
                if row[column]:
                    kwargs[column] = row[column]
            except (IndexError, KeyError):
                pass
        return await publisher(
            bot,
            json.loads(row["media_json"] or "[]"),
            json.loads(row["documents_json"] or "[]"),
            **kwargs,
        )

    async def list_pending(self, *, limit: int = 20, cursor: Optional[str] = None) -> Dict[str, Any]:
        limit = max(1, min(int(limit or 20), 100))
        created_cursor: Optional[float] = None
        id_cursor: Optional[int] = None
        if cursor:
            match = re.fullmatch(r"(\d+(?:\.\d+)?):(\d+)", cursor)
            if not match:
                raise ReviewError("invalid cursor", details={"cursor": cursor})
            created_cursor, id_cursor = float(match.group(1)), int(match.group(2))

        rows = await self._repo.list_pending(
            limit=limit + 1,
            created_cursor=created_cursor,
            id_cursor=id_cursor,
        )
        has_more = len(rows) > limit
        rows = rows[:limit]
        items = [
            ReviewSummary(
                review_id=row["id"],
                title=row["title"] or "",
                tags=_tags(row["tags"] or ""),
                media_count=len(json.loads(row["media_json"] or "[]")),
                document_count=len(json.loads(row["documents_json"] or "[]")),
                spoiler=bool(row["spoiler"]),
                source_label=row["source_label"] or None,
                created_at=_iso(row["created_at"]) or "",
                status="pending",
            ).to_dict()
            for row in rows
        ]
        next_cursor = None
        if has_more and rows:
            next_cursor = f"{rows[-1]['created_at']}:{rows[-1]['id']}"
        return {"items": items, "next_cursor": next_cursor}

    async def get_row(self, review_id: int):
        return await self._repo.get(int(review_id))

    async def get_review(self, review_id: int) -> ReviewItem:
        row = await self.get_row(int(review_id))
        if row is None:
            raise ReviewNotFoundError("审核记录不存在")
        return _to_item(row)

    async def set_spoiler(self, review_id: int, spoiler: bool, *,
                          actor: Any = None, source: str = "service") -> ActionResult:
        changed, row = await self._repo.set_spoiler(int(review_id), spoiler)
        if row is None:
            raise ReviewNotFoundError("审核记录不存在")
        if not changed:
            if row["status"] == "publishing":
                raise ReviewBusyError(
                    "Review is currently being processed",
                    details={"status": row["status"]},
                )
            raise ReviewStateError(f"该投稿当前状态：{row['status']}")
        _audit("set_spoiler", int(review_id), actor, "ok",
               source=source, spoiler=bool(spoiler))
        return ActionResult(int(review_id), row["status"], False)

    async def toggle_spoiler(self, review_id: int, *, actor: Any = None) -> ActionResult:
        changed, row = await self._repo.toggle_spoiler(int(review_id))
        if row is None:
            raise ReviewNotFoundError("审核记录不存在")
        if not changed:
            if row["status"] == "publishing":
                raise ReviewBusyError(
                    "Review is currently being processed",
                    details={"status": row["status"]},
                )
            raise ReviewStateError(f"该投稿当前状态：{row['status']}")
        _audit("toggle_spoiler", int(review_id), actor, "ok")
        return ActionResult(int(review_id), row["status"], False, link=row["link"])

    async def reject(self, bot, review_id: int, *, reason: Optional[str] = None,
                     actor: Any = None, source: str = "service",
                     notify_chat_submitter: bool = True) -> ActionResult:
        safe_reason = clean_reason(reason)
        changed, row = await self._repo.reject(int(review_id), actor=actor)
        if row is None:
            raise ReviewNotFoundError("审核记录不存在")
        if not changed:
            if row["status"] == "publishing":
                raise ReviewBusyError(
                    "Review is currently being processed",
                    details={"status": row["status"]},
                )
            raise ReviewStateError(f"该投稿当前状态：{row['status']}")

        if notify_chat_submitter and row["source"] == "chat":
            try:
                await bot.send_message(
                    chat_id=row["user_id"],
                    text="❌ 你的投稿未通过审核。如需了解原因，请联系频道管理员。",
                )
            except Exception:
                logger.warning("通知聊天投稿人拒绝结果失败: review_id=%s",
                               review_id, exc_info=True)
        _audit("reject", int(review_id), actor, "ok",
               source=source, reason=safe_reason or None)
        return ActionResult(int(review_id), "rejected", False)

    def failure_hint(self, error: BaseException) -> str:
        try:
            from handlers.publish import DiscussionPublishError
            from telegram.error import NetworkError
        except Exception:
            return "发布失败，可重试"
        if isinstance(error, DiscussionPublishError):
            if getattr(error, "uncertain", False):
                return (
                    "评论区发布结果不确定：请到频道确认首贴、到该帖评论串确认图片是否齐；"
                    "确认缺图后再点重试（重试只补发，重复请手动删多余相册）"
                )
            return "发布失败，已自动重试一次仍未成功，可再点重试"
        if isinstance(error, NetworkError):
            return "发送结果不确定，请先检查频道；确认未发布后再重试"
        return "发布失败，可重试"

    async def approve(self, bot, review_id: int, *,
                      spoiler: Optional[bool] = None,
                      actor: Any = None, source: str = "service",
                      notify_chat_submitter: bool = True,
                      on_claim: Optional[Callable[[Any], Awaitable[None]]] = None
                      ) -> ActionResult:
        review_id = int(review_id)
        claimed, row = await self._repo.claim_for_publishing(
            review_id, stale_seconds=PUBLISHING_STALE_SECONDS, spoiler=spoiler
        )
        if row is None:
            raise ReviewNotFoundError("审核记录不存在")
        if not claimed:
            if row["status"] == "published":
                return ActionResult(
                    review_id, "published", True,
                    row["published_message_id"], None,
                )
            if row["status"] == "publishing":
                raise ReviewBusyError(
                    "Review is currently being processed",
                    details={"status": row["status"]},
                )
            raise ReviewStateError(f"该投稿当前状态：{row['status']}")

        current_spoiler = bool(row["spoiler"]) if spoiler is None else bool(spoiler)
        if on_claim is not None:
            try:
                await on_claim(row)
            except Exception:
                logger.debug("审核 claim UI 通知失败: review_id=%s", review_id,
                             exc_info=True)

        try:
            result = await self._publish(bot, row, current_spoiler)
        except Exception as error:
            logger.error("审核通过后发布失败: review_id=%s", review_id, exc_info=True)
            await self._repo.mark_failed(review_id, str(error))
            _audit("approve", review_id, actor, "failed",
                   source=source, error=str(error)[:120])
            raise PublishFailedError(
                str(error)[:200],
                retry_hint=self.failure_hint(error),
                original=error,
            )

        # Guarded terminal transition: a stale reclaim racing this late writer
        # cannot be clobbered – if we no longer own the claim, the row was
        # already resolved by another actor and we must not overwrite it.
        marked = await self._repo.mark_published(
            review_id, actor=actor, message_id=result["message_id"]
        )
        if not marked:
            current = await self.get_row(review_id)
            logger.warning(
                "发布成功但状态已被其他执行者推进: review_id=%s status=%s",
                review_id, current["status"] if current else "missing",
            )
            return ActionResult(
                review_id,
                current["status"] if current else "published",
                True,
                result.get("message_id"), result.get("link"),
            )

        if notify_chat_submitter and row["source"] == "chat":
            try:
                await bot.send_message(
                    chat_id=row["user_id"],
                    text=f"✅ 你的投稿已通过审核并发布到频道。\n{result.get('link', '')}",
                )
            except Exception:
                logger.warning("通知聊天投稿人通过结果失败: review_id=%s",
                               review_id, exc_info=True)
        _audit("approve", review_id, actor, "published", source=source)
        return ActionResult(
            review_id, "published", False,
            result.get("message_id"), result.get("link"),
        )

    async def get_media(self, bot, review_id: int, index: int,
                        variant: str = "preview") -> MediaResult:
        if variant not in {"thumbnail", "preview", "original"}:
            raise ReviewError("invalid variant")
        item = await self.get_review(review_id)
        if index < 0 or index >= len(item.media):
            raise MediaNotFoundError("媒体不存在")
        media = item.media[index]
        if not media.file_id:
            raise MediaNotFoundError("媒体 file_id 不存在")
        if media.kind in {"document", "audio"}:
            raise PreviewUnavailableError(
                "文档/音频不返回内容；请使用 get_review 查看元数据"
            )

        if media.kind == "video" and variant == "original":
            raise PreviewUnavailableError("视频 original 不直接返回；请使用 preview 封面")
        use_thumbnail_file_id = (
            variant in {"thumbnail", "preview"}
            and media.kind in {"video", "animation"}
            and media.thumbnail_file_id
        )
        if media.kind in {"video", "animation"} and not use_thumbnail_file_id:
            raise PreviewUnavailableError("Telegram 未提供可用封面；第一版不抽帧")
        file_id = media.thumbnail_file_id if use_thumbnail_file_id else media.file_id
        tg_file = await bot.get_file(file_id)
        size = int(getattr(tg_file, "file_size", 0) or 0)
        if size > MEDIA_DOWNLOAD_MAX_BYTES:
            raise PreviewUnavailableError("媒体超过安全下载大小")
        buffer = io.BytesIO()
        await tg_file.download_to_memory(buffer)
        data = buffer.getvalue()
        if not data or len(data) > MEDIA_DOWNLOAD_MAX_BYTES:
            raise PreviewUnavailableError("媒体不可用或超过安全下载大小")

        remote_path = getattr(tg_file, "file_path", "") or ""
        filename = media.filename or remote_path.rsplit("/", 1)[-1] or f"media-{index}"
        if variant == "original":
            mime_type = self._mime_for(media.kind, filename, data)
            return MediaResult(review_id, index, media.kind, "original", mime_type,
                               filename, len(data), data, media.kind)

        image_data, mime_type = self._image_preview(data, variant)
        return MediaResult(
            review_id, index, "image", variant, mime_type,
            f"{os.path.splitext(filename)[0]}.jpg", len(image_data),
            image_data, media.kind,
        )

    @staticmethod
    def _mime_for(kind: str, filename: str, data: bytes) -> str:
        ext = os.path.splitext(filename or "")[1].lower()
        return {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".webp": "image/webp",
            ".gif": "image/gif",
            ".mp4": "video/mp4",
            ".webm": "video/webm",
            ".mov": "video/quicktime",
            ".mp3": "audio/mpeg",
            ".ogg": "audio/ogg",
            ".m4a": "audio/mp4",
        }.get(ext, {
            "photo": "image/jpeg",
            "animation": "image/gif",
            "video": "video/mp4",
            "audio": "audio/mpeg",
        }.get(kind, "application/octet-stream"))

    @staticmethod
    def _image_preview(data: bytes, variant: str):
        try:
            from PIL import Image
        except ImportError as exc:
            raise PreviewUnavailableError("服务器未安装图片预览依赖") from exc
        try:
            image = Image.open(io.BytesIO(data))
            image.load()
        except Exception as exc:
            raise PreviewUnavailableError("无法生成图片预览") from exc
        if getattr(image, "is_animated", False):
            image.seek(0)
        if image.mode != "RGB":
            image = image.convert("RGB")
        long_edge = THUMBNAIL_LONG_EDGE if variant == "thumbnail" else PREVIEW_LONG_EDGE
        image.thumbnail((long_edge, long_edge), Image.LANCZOS)
        out = io.BytesIO()
        image.save(out, format="JPEG", quality=88, optimize=True)
        return out.getvalue(), "image/jpeg"


def load_review_policy() -> str:
    path = os.getenv("TELEPOST_REVIEW_POLICY", "config/review_policy.md")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return (
            "# TelePost Review Policy\n\n"
            "管理员尚未配置具体审核规则。请只根据频道主题、法律合规性、版权、广告引流和"
            "明显内容安全风险给出谨慎建议；不确定时选择 needs_human_review。\n"
        )
