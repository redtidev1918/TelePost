"""Native bot automation handler: /schedule command and inline callbacks.

Admin-only. Declarative action types only. No eval.
"""
from __future__ import annotations

import html as _html
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from utils.blacklist import is_owner
from telepost.domain.automation import (
    AutomationArgError,
    parse_automation_add,
    ACTION_HOT,
)
from telepost.storage.sqlite import automation as automation_store
from telepost.observability.audit import record_event

logger = logging.getLogger(__name__)


def _is_admin(update: Update) -> bool:
    user = update.effective_user
    return bool(user and is_owner(user.id))


def _deny(update: Update) -> None:
    update.effective_message.reply_text("⛔ 此命令仅限管理员使用")


async def schedule_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/schedule — 管理定时任务。"""
    if not _is_admin(update):
        _deny(update)
        return

    args = context.args or []
    if not args:
        await _list_tasks(update, context)
        return

    sub = args[0].lower().strip()
    rest = args[1:]

    if sub == "list":
        await _list_tasks(update, context)
    elif sub == "add":
        await _add_task(update, context, rest)
    elif sub == "enable":
        await _toggle_task(update, context, rest, enabled=True)
    elif sub == "disable":
        await _toggle_task(update, context, rest, enabled=False)
    elif sub == "delete":
        await _delete_task(update, context, rest)
    elif sub == "run":
        await _run_task(update, context, rest)
    elif sub == "preview":
        await _preview_task(update, context, rest)
    else:
        update.effective_message.reply_text(
            "⏰ 定时任务\n\n"
            "/schedule list — 查看所有任务\n"
            "/schedule add weekly-hot <星期> <HH:MM> <TOP N>\n"
            "  例：/schedule add weekly-hot sunday 20:00 10\n"
            "/schedule enable <id> — 启用\n"
            "/schedule disable <id> — 停用\n"
            "/schedule run <id> — 立即执行\n"
            "/schedule preview <id> — 预览输出\n"
            "/schedule delete <id> — 删除"
        )


async def _list_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    tasks = await automation_store.list_tasks()
    if not tasks:
        update.effective_message.reply_text(
            "⏰ 还没有定时任务。\n\n"
            "创建：/schedule add weekly-hot sunday 20:00 10"
        )
        return

    from telepost.domain.hot import active_timezone
    tz = active_timezone()

    lines = ["⏰ 定时任务\n"]
    for i, t in enumerate(tasks, 1):
        status = "✅ 已启用" if t.enabled else "⛔ 已停用"
        last = "—" if not t.last_run_at else _fmt_ts(t.last_run_at)
        last_status = t.last_run_status or "—"
        next_run = t.next_run_at()
        next_str = "—" if not next_run else _fmt_ts(next_run)
        lines.append(
            f"{i}. {t.action_label}\n"
            f"   ⏰ {t.schedule_label}  {status}\n"
            f"   上次：{last} {last_status}\n"
            f"   下次：{next_str}\n"
        )
        # Buttons per task
    text = "\n".join(lines)

    keyboard = []
    for t in tasks:
        row = []
        if t.enabled:
            row.append(InlineKeyboardButton("⏸ 停用", callback_data=f"autod_{t.id}"))
        else:
            row.append(InlineKeyboardButton("▶️ 启用", callback_data=f"autoe_{t.id}"))
        row.append(InlineKeyboardButton("▶️ 执行", callback_data=f"autorun_{t.id}"))
        row.append(InlineKeyboardButton("🗑 删除", callback_data=f"autodel_{t.id}"))
        keyboard.append(row)

    update.effective_message.reply_text(
        text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard) if keyboard else None,
    )


async def _add_task(update: Update, context: ContextTypes.DEFAULT_TYPE, args: list) -> None:
    from config.settings import REVIEW_CHAT_ID, CHANNEL_ID
    target = str(REVIEW_CHAT_ID or CHANNEL_ID)
    try:
        task = parse_automation_add(args, created_by=update.effective_user.id, target_chat_id=target)
    except AutomationArgError as e:
        update.effective_message.reply_text(f"⚠️ {e}")
        return

    next_run = task.next_run_at()
    preview = (
        f"⏰ 新任务预览\n\n"
        f"任务：{task.action_label}\n"
        f"执行：{task.schedule_label}\n"
        f"目标：审核群\n"
        f"时区：{task.timezone}\n"
        f"下次：{_fmt_ts(next_run) if next_run else '—'}\n\n"
        f"确认创建？"
    )
    # Store the pending task in user_data for the confirm callback
    context.user_data["pending_automation"] = task.to_json()
    update.effective_message.reply_text(
        preview,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ 确认创建", callback_data="autoadd_confirm"),
            InlineKeyboardButton("❌ 取消", callback_data="autoadd_cancel"),
        ]]),
    )


async def _toggle_task(update: Update, context: ContextTypes.DEFAULT_TYPE, args: list, *, enabled: bool) -> None:
    if not args or not args[0].isdigit():
        update.effective_message.reply_text(f"用法：/schedule {'enable' if enabled else 'disable'} <id>")
        return
    task_id = int(args[0])
    ok = await automation_store.set_enabled(task_id, enabled)
    if not ok:
        update.effective_message.reply_text(f"❌ 任务 {task_id} 不存在")
        return
    await record_event("automation_toggle", task_id=task_id, enabled=enabled, actor_id=update.effective_user.id)
    await _sync(update, context)
    update.effective_message.reply_text(f"✅ 任务 {task_id} {'已启用' if enabled else '已停用'}")


async def _delete_task(update: Update, context: ContextTypes.DEFAULT_TYPE, args: list) -> None:
    if not args or not args[0].isdigit():
        update.effective_message.reply_text("用法：/schedule delete <id>")
        return
    task_id = int(args[0])
    ok = await automation_store.delete_task(task_id)
    if not ok:
        update.effective_message.reply_text(f"❌ 任务 {task_id} 不存在")
        return
    await record_event("automation_delete", task_id=task_id, actor_id=update.effective_user.id)
    await _sync(update, context)
    update.effective_message.reply_text(f"✅ 任务 {task_id} 已删除")


async def _run_task(update: Update, context: ContextTypes.DEFAULT_TYPE, args: list) -> None:
    if not args or not args[0].isdigit():
        update.effective_message.reply_text("用法：/schedule run <id>")
        return
    task_id = int(args[0])
    task = await automation_store.get_task(task_id)
    if not task:
        update.effective_message.reply_text(f"❌ 任务 {task_id} 不存在")
        return
    from telepost.application.automation import manual_run
    status, error = await manual_run(task, context.bot)
    await record_event("automation_manual_run", task_id=task_id, status=status, error=error, actor_id=update.effective_user.id)
    if status == "success":
        update.effective_message.reply_text(f"✅ 任务 {task_id} 已执行")
    else:
        update.effective_message.reply_text(f"⚠️ 任务 {task_id} 执行失败：{error or status}")


async def _preview_task(update: Update, context: ContextTypes.DEFAULT_TYPE, args: list) -> None:
    if not args or not args[0].isdigit():
        update.effective_message.reply_text("用法：/schedule preview <id>")
        return
    task_id = int(args[0])
    task = await automation_store.get_task(task_id)
    if not task:
        update.effective_message.reply_text(f"❌ 任务 {task_id} 不存在")
        return
    if task.action_type != ACTION_HOT:
        update.effective_message.reply_text("⚠️ 该任务类型暂不支持预览")
        return
    payload = task.action_payload or {}
    from telepost.domain.hot import HotQuery
    from handlers.stats_handlers import build_hot_message
    hq = HotQuery(scope=payload.get("scope", "all"), limit=payload.get("limit", 10))
    text = await build_hot_message(hq)
    update.effective_message.reply_text(text, parse_mode="HTML", disable_web_page_preview=True)


async def _sync(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Re-sync JobQueue after any task mutation."""
    from telepost.application.automation import sync_jobqueue
    app = context.application
    await sync_jobqueue(app)


# ─── Callback handlers ───

async def handle_automation_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    data = query.data or ""
    if not _is_admin(update):
        await query.answer("⛔ 仅管理员", show_alert=True)
        return

    if data == "autoadd_confirm":
        pending = context.user_data.get("pending_automation")
        if not pending:
            await query.answer("预览已过期，请重新创建", show_alert=True)
            return
        task = AutomationTask(**pending)
        created = await automation_store.create_task(task)
        context.user_data.pop("pending_automation", None)
        await record_event("automation_create", task_id=created.id, actor_id=update.effective_user.id)
        await _sync(update, context)
        await query.edit_message_text(
            f"✅ 任务已创建（#{created.id}）\n"
            f"下次执行：{_fmt_ts(created.next_run_at())}"
        )
    elif data == "autoadd_cancel":
        context.user_data.pop("pending_automation", None)
        await query.edit_message_text("已取消")
    elif data.startswith("autoe_"):
        task_id = int(data.replace("autoe_", ""))
        await automation_store.set_enabled(task_id, True)
        await _sync(update, context)
        await query.answer("✅ 已启用")
        await query.edit_message_text(query.message.text + "\n\n✅ 已启用", parse_mode="HTML")
    elif data.startswith("autod_"):
        task_id = int(data.replace("autod_", ""))
        await automation_store.set_enabled(task_id, False)
        await _sync(update, context)
        await query.answer("⏸ 已停用")
    elif data.startswith("autorun_"):
        task_id = int(data.replace("autorun_", ""))
        task = await automation_store.get_task(task_id)
        if not task:
            await query.answer("任务不存在", show_alert=True)
            return
        from telepost.application.automation import manual_run
        status, error = await manual_run(task, context.bot)
        await record_event("automation_manual_run", task_id=task_id, status=status, error=error, actor_id=update.effective_user.id)
        await query.answer("✅ 已执行" if status == "success" else f"⚠️ {error or status}", show_alert=(status != "success"))
    elif data.startswith("autodel_"):
        task_id = int(data.replace("autodel_", ""))
        ok = await automation_store.delete_task(task_id)
        await _sync(update, context)
        if ok:
            await record_event("automation_delete", task_id=task_id, actor_id=update.effective_user.id)
            await query.answer("🗑 已删除")
            await query.edit_message_text(query.message.text + "\n\n🗑 已删除", parse_mode="HTML")
        else:
            await query.answer("任务不存在", show_alert=True)


def _fmt_ts(ts) -> str:
    from datetime import datetime
    from telepost.domain.hot import active_timezone
    if not ts:
        return "—"
    return datetime.fromtimestamp(ts, tz=active_timezone()).strftime("%m-%d %H:%M")
