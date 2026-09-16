"""Review-chat UI: inline keyboard + control card text (PTB adapter)."""
from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from ..application.review_queue import pixiv_id_from_link
from ..domain.presentation import submitter_display


def review_keyboard(review_id: int, link: str = "", *,
                    spoiler: bool = False, source: str = "api",
                    pixiv_id: str = "", failed: bool = False) -> InlineKeyboardMarkup:
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
    # §submission-entrypoint: the public submission CTA (✉️ TG 投稿 / 📱 Mini App)
    # belongs ONLY to final Channel Publication footers, never to Review/staging/
    # moderation messages. Removing it here keeps a single, consistent UI rule.
    return InlineKeyboardMarkup(rows)


def source_label(source: str) -> str:
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
