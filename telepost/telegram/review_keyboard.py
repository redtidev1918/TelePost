"""Review-chat UI: inline keyboard + control card text (PTB adapter)."""
from __future__ import annotations
from ui.i18n import tr

import json
from typing import Optional, Tuple

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from ..application.review_queue import work_id_from_link
from ..domain.presentation import submitter_display


def review_keyboard(review_id: int, link: str = "", *,
                    spoiler: bool = False, source: str = "api",
                    work_id: str = "", failed: bool = False,
                    submitter_user_id=None, actor_kind: str = "user",
                    actor_subject: str = "") -> InlineKeyboardMarkup:
    approve_label = tr('🔄 重试发布') if failed else tr('✅ 发布到频道')
    rows = [[
        InlineKeyboardButton(approve_label, callback_data=f"review_approve:{review_id}"),
        InlineKeyboardButton(tr('❌ 拒绝'), callback_data=f"review_reject:{review_id}"),
    ]]
    rows.append([
        InlineKeyboardButton(
            tr('🔇 遮罩：{p0}').format(p0=tr('开') if spoiler else tr('关')),
            callback_data=f"review_spoiler:{review_id}",
        ),
    ])
    if source == "api" and work_id:
        rows[-1].append(
            InlineKeyboardButton(
                tr('🔄 重抓/换一张'),
                callback_data=f"review_refetch:{review_id}",
            )
        )
    if link:
        rows.append([InlineKeyboardButton(tr('🔗 查看原链接'), url=link)])
    # §moderation: operator governance is a first-class review action. Only
    # subjects that exist on the card get a button (human submitter and/or
    # API/service actor); everything that is blocked is recorded who/when/why.
    mod_row = []
    if submitter_user_id:
        mod_row.append(InlineKeyboardButton(tr('🚫 封禁投稿人'),
                                            callback_data=f"review_block_user:{review_id}"))
    if source == "api" or actor_kind == "service":
        mod_row.append(InlineKeyboardButton(tr('🔑 禁用API'),
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
    return tr('Telegram 聊天') if source == "chat" else "HTTP API"


def control_text(*, review_id: int, command, media_count: int,
                 document_count: int, original_count: int = 0) -> str:
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
        submitter_line = tr("""投稿人：{p0}
""").format(p0=display)
    # 文件行：媒体写真 + 文档；保留的「原图」单独标注，避免用户误以为
    # 内容重复（例如因分辨率超限而原样保留为文档的原图副本）。
    documents_bit = tr('{p0} 个文档').format(p0=document_count)
    if document_count and original_count:
        documents_bit = (
            tr('{p0} 个文档（含 {p1} 份原图）').format(p0=document_count, p1=original_count)
        )
    return (
        tr("""🕵️ 投稿待审核 #{p0}
""").format(p0=review_id)
        + provenance_line
        + tr("""投稿方式：{p0}
""").format(p0=source_label(command.source))
        + submitter_line
        + tr("""标题：{p0}
标签：{p1}
文件：{p2} 个媒体 / {p3}""").format(p0=command.title or tr('（无）'), p1=command.tags or tr('（无）'), p2=media_count, p3=documents_bit)
    )


def superseded_notice_text(*, new_review_id: int, source_review_id: int = 0) -> str:
    """Text that replaces a superseded review card (actions removed).

    Superseded ≠ rejected: the generation was replaced by a refetch result, so
    the card becomes history and points at the new chain head."""
    head = tr("""♻️ 此审核稿已被重抓结果替代

新的审核稿：#{p0}""").format(p0=new_review_id)
    if source_review_id:
        head += tr('（原审核 #{p0}）').format(p0=source_review_id)
    return head + tr("""
请在新审核稿上继续审核。""")


def reused_notice_text(row) -> str:
    labels = {
        "pending": tr('待审核'),
        "failed": tr('发布失败，可重试'),
        "published": tr('已发布，本次未重复发布'),
    }
    return (
        tr("""♻️ 收到重复投稿，已复用现有审核
审核：#{p0}
状态：{p1}
媒体未重复上传，原审核记录仍有效。""").format(p0=row['id'], p1=labels.get(row['status'], row['status']))
    )


def refetch_pending_text(*, review_id: int, minutes: Optional[int] = None,
                         stage_label: str = "", task_id: str = "") -> str:
    """Control-card text while a manual replacement (重抓) is running.

    §refetch-card-state: pressing 重抓 means the CURRENT candidate is rejected,
    so the post the operator clicked must say so immediately instead of staying
    byte-identical until a replacement lands (or forever, when none is found).
    The card also carries the lifecycle facts the operator asks for — the
    current stage, how long it has been waiting, and the quotable task id."""
    progress = ""
    if stage_label:
        progress += tr("""
当前阶段：{p0}""").format(p0=stage_label)
    if minutes:
        progress += tr("""
已等待约 {p0} 分钟，仍在查找…""").format(p0=minutes)
    if task_id:
        progress += tr("""
任务ID：{p0}""").format(p0=task_id)
    return (
        tr("""🔄 审核 #{p0} 已提交重抓

当前候选已作废（视为已拒绝），不会再被发布。
正在查找新的候选作品…通常 1–3 分钟。
找到后会自动替换进审核队列；没有找到时本卡片会恢复。""").format(p0=review_id)
        + progress
    )


def refetch_pending_keyboard(review_id: int,
                             link: str = "") -> InlineKeyboardMarkup:
    """Keyboard while a refetch is running: publish/reject are gone on purpose."""
    rows = [[InlineKeyboardButton(tr('🔄 重抓/换一张'),
                                  callback_data=f"review_refetch:{review_id}")]]
    if link:
        rows.append([InlineKeyboardButton(tr('🔗 查看原链接'), url=link)])
    return InlineKeyboardMarkup(rows)


def refetch_failure_reason(attempt_row) -> str:
    """A short human-readable reason for a terminally-failed refetch attempt.

    ``attempt_row`` is a ``refetch_attempts`` row (sqlite Row, index access);
    the terminal state and the optional failure code are combined into a compact
    label. Never raises on a missing/odd row.
    """
    def _get(row, key):
        try:
            return row[key] if (key in row.keys()) else None
        except Exception:
            return None
    state = _get(attempt_row, "state")
    if not state or state == "no_candidate":
        base = tr('未找到新的可替换作品')
    else:
        label = {
            "failed": tr('重抓提交失败'),
            "timeout": tr('重抓超时'),
            "cancelled": tr('重抓已取消'),
            "replaced": tr('已由新作品替代'),
        }.get(state, tr('重抓结束（{p0}）').format(p0=state))
        base = label
    code = _get(attempt_row, "failure_code")
    return f"{base}：{code}" if code else base


def refetch_voided_text(*, review_id: int, reason: str = "",
                        task_id: str = "") -> str:
    """Control-card text after a refetch ended WITHOUT a replacement.

    §refetch-card-state: pressing 重抓 already rejects the current candidate, so
    a terminal refetch that found nothing must NOT resurrect the publish/reject
    buttons — the operator decided to drop this work, and the only way forward
    is another 重抓 (which fetches a NEW work with an incremented sequence).
    ``reason``/``task_id`` are the terminal facts surfaced on the card.
    """
    detail = ""
    if reason:
        detail += tr("""
原因：{p0}""").format(p0=reason)
    if task_id:
        detail += tr("""
任务ID：{p0}""").format(p0=task_id)
    return (
        tr("""🕳️ 审核 #{p0} 已作废（视为已拒绝）

当前候选不会再被发布。
上次重抓未能找到新的可替换作品。
可再次点击「重抓/换一张」直接抓取一个新作品（序号递增），找到后会作为新的审核稿。""").format(p0=review_id)
        + detail
    )


def refetch_voided_keyboard(review_id: int,
                            link: str = "") -> InlineKeyboardMarkup:
    """Keyboard for a voided refetch card: keep 重抓 + original link only."""
    rows = [[InlineKeyboardButton(tr('🔄 重抓/换一张'),
                                  callback_data=f"review_refetch:{review_id}")]]
    if link:
        rows.append([InlineKeyboardButton(tr('🔗 查看原链接'), url=link)])
    return InlineKeyboardMarkup(rows)


def control_card_from_row(row) -> Tuple[str, InlineKeyboardMarkup]:
    """Rebuild the NORMAL review card (text + keyboard) from a review row.

    Used to (re)render a card that has never been refetched — a plain pending
    candidate stays an ordinary, fully-actionable review. Mirrors
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
        work_id=(command.work_id or work_id_from_link(command.link or "")),
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
