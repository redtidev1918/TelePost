"""Review-chat UI: inline keyboard + control card text (PTB adapter)."""
from __future__ import annotations

import json
from typing import Optional, Tuple

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from ..application.review_queue import pixiv_id_from_link
from ..domain.presentation import submitter_display


def review_keyboard(review_id: int, link: str = "", *,
                    spoiler: bool = False, source: str = "api",
                    pixiv_id: str = "", failed: bool = False,
                    submitter_user_id=None, actor_kind: str = "user",
                    actor_subject: str = "") -> InlineKeyboardMarkup:
    approve_label = "🔄 重试发布" if failed else "✅ 发布到频道"
    rows = [[
        InlineKeyboardButton(approve_label, callback_data=f"review_approve:{review_id}"),
        InlineKeyboardButton("❌ 拒绝", callback_data=f"review_reject:{review_id}"),
    ]]
    rows.append([
        InlineKeyboardButton(
            f"🔇 遮罩：{'开' if spoiler else '关'}",
            callback_data=f"review_spoiler:{review_id}",
        ),
    ])
    if source == "api" and pixiv_id:
        rows[-1].append(
            InlineKeyboardButton(
                "🔄 重抓/换一张",
                callback_data=f"review_refetch:{review_id}",
            )
        )
    if link:
        rows.append([InlineKeyboardButton("🔗 查看原链接", url=link)])
    # §moderation: operator governance is a first-class review action. Only
    # subjects that exist on the card get a button (human submitter and/or
    # API/service actor); everything that is blocked is recorded who/when/why.
    mod_row = []
    if submitter_user_id:
        mod_row.append(InlineKeyboardButton("🚫 封禁投稿人",
                                            callback_data=f"review_block_user:{review_id}"))
    if source == "api" or actor_kind == "service":
        mod_row.append(InlineKeyboardButton("🔑 禁用API",
                                            callback_data=f"review_block_api:{review_id}"))
    if mod_row:
        rows.append(mod_row)
    # §submission-entrypoint: the public submission CTA (✉️ TG 投稿 / 📱 Mini App)
    # belongs ONLY to final Channel Publication footers, never to Review/staging/
    # moderation messages. Removing it here keeps a single, consistent UI rule.
    return InlineKeyboardMarkup(rows)


def source_label(source: str) -> str:
    if source == "miniapp":
        return "Mini App"
    return "Telegram 聊天" if source == "chat" else "HTTP API"


def control_text(*, review_id: int, command, media_count: int,
                 document_count: int) -> str:
    provenance_line = ""
    if command.source_label:
        provenance_line = f"🏷️ {command.source_label}\n"
    elif command.scheduled_at:
        provenance_line = f"🕐 {command.scheduled_at}\n"
    # Only an explicit human submitter is presented as 投稿人 (§identity).
    # Service/API rows (submitter_user_id NULL) have no author; the card still
    # shows 投稿方式 + provenance instead of a token name.
    submitter_line = ""
    display = submitter_display(
        getattr(command, "submitter_user_id", None),
        getattr(command, "submitter_username", ""),
        getattr(command, "submitter_display_name", ""),
    )
    if display:
        submitter_line = f"投稿人：{display}\n"
    return (
        f"🕵️ 投稿待审核 #{review_id}\n"
        + provenance_line
        + f"投稿方式：{source_label(command.source)}\n"
        + submitter_line
        + f"标题：{command.title or '（无）'}\n"
        f"标签：{command.tags or '（无）'}\n"
        f"文件：{media_count} 个媒体 / {document_count} 个文档"
    )


def superseded_notice_text(*, new_review_id: int, source_review_id: int = 0) -> str:
    """Text that replaces a superseded review card (actions removed).

    Superseded ≠ rejected: the generation was replaced by a refetch result, so
    the card becomes history and points at the new chain head."""
    head = f"♻️ 此审核稿已被重抓结果替代\n\n新的审核稿：#{new_review_id}"
    if source_review_id:
        head += f"（原审核 #{source_review_id}）"
    return head + "\n请在新审核稿上继续审核。"


def reused_notice_text(row) -> str:
    labels = {
        "pending": "待审核",
        "failed": "发布失败，可重试",
        "published": "已发布，本次未重复发布",
    }
    return (
        "♻️ 收到重复投稿，已复用现有审核\n"
        f"审核：#{row['id']}\n"
        f"状态：{labels.get(row['status'], row['status'])}\n"
        "媒体未重复上传，原审核记录仍有效。"
    )


def refetch_pending_text(*, review_id: int, minutes: Optional[int] = None) -> str:
    """Control-card text while a manual replacement (重抓) is running.

    §refetch-card-state: pressing 重抓 means the CURRENT candidate is rejected,
    so the post the operator clicked must say so immediately instead of staying
    byte-identical until a replacement lands (or forever, when none is found)."""
    waited = f"\n已等待约 {minutes} 分钟，仍在查找…" if minutes else ""
    return (
        f"🔄 审核 #{review_id} 已提交重抓\n\n"
        "当前候选已作废（视为已拒绝），不会再被发布。\n"
        "正在查找新的候选作品…通常 1–3 分钟。\n"
        "找到后会自动替换进审核队列；没有找到时本卡片会恢复。"
        + waited
    )


def refetch_pending_keyboard(review_id: int,
                             link: str = "") -> InlineKeyboardMarkup:
    """Keyboard while a refetch is running: publish/reject are gone on purpose."""
    rows = [[InlineKeyboardButton("🔄 重抓/换一张",
                                  callback_data=f"review_refetch:{review_id}")]]
    if link:
        rows.append([InlineKeyboardButton("🔗 查看原链接", url=link)])
    return InlineKeyboardMarkup(rows)


def control_card_from_row(row) -> Tuple[str, InlineKeyboardMarkup]:
    """Rebuild the NORMAL review card (text + keyboard) from a review row.

    Used to restore a card after a refetch reached a terminal state without a
    replacement, so the candidate becomes actionable again. Mirrors
    ``TelegramReviewStager.send_control_message_id``; the row is the source of
    truth (the card must survive a process restart)."""
    from ..application.review_queue import command_from_row

    command = command_from_row(row)
    media = _json_list(row, "media_json")
    documents = _json_list(row, "documents_json")
    text = control_text(
        review_id=int(row["id"]), command=command,
        media_count=len(media), document_count=len(documents),
    )
    markup = review_keyboard(
        int(row["id"]),
        command.link,
        spoiler=bool(command.spoiler),
        source=command.source,
        pixiv_id=(command.pixiv_id or pixiv_id_from_link(command.link or "")),
        failed=(row["status"] == "failed"
                if "status" in _row_keys(row) else False),
        submitter_user_id=command.submitter_user_id,
        actor_kind=command.actor_kind,
        actor_subject=command.actor_subject,
    )
    return text, markup


def _row_keys(row) -> list:
    try:
        return list(row.keys())
    except AttributeError:
        return list(row)


def _json_list(row, column: str) -> list:
    keys = _row_keys(row)
    if column not in keys:
        return []
    try:
        value = json.loads(row[column] or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    return value if isinstance(value, list) else []
