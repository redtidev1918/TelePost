"""Native bot automation handler: /schedule command and inline callbacks.

Admin-only. Declarative action types only. No eval.
"""
from __future__ import annotations
from ui.i18n import tr

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
    if not user:
        return False
    if is_owner(user.id):
        return True
    from config.settings import ADMIN_IDS
    return user.id in (ADMIN_IDS or [])


async def _deny(update: Update) -> None:
    await update.effective_message.reply_text(tr('⛔ 此命令仅限管理员使用'))


async def schedule_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/schedule — 管理定时任务。"""
    if not _is_admin(update):
        await _deny(update)
        return

    try:
        await automation_store.ensure_tables()
    except Exception as exc:
        logger.error('automation ensure_tables failed: %s', exc, exc_info=True)
        await update.effective_message.reply_text(tr('定时任务表初始化失败，请检查日志'))
        return

    try:
        await automation_store.ensure_tables()
    except Exception as exc:
        logger.error('automation ensure_tables failed: %s', exc, exc_info=True)
        await update.effective_message.reply_text(tr('定时任务表初始化失败，请检查日志'))
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
        await update.effective_message.reply_text(
            tr("""⏰ 定时任务

/schedule — 查看所有任务
/schedule add weekly-hot <星期> <HH:MM> <TOP N>
  例：/schedule add weekly-hot sunday 20:00 10
/schedule enable <id> — 启用
/schedule disable <id> — 停用
/schedule run <id> — 立即执行
/schedule preview <id> — 预览输出
/schedule delete <id> — 删除""")
        )


async def _list_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    tasks = await automation_store.list_tasks()
    if not tasks:
        await update.effective_message.reply_text(
            tr("""⏰ 定时任务

还没有任务。

点击下方按钮创建，或发送 /schedule add weekly-hot 星期日 20:00 10"""),
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(tr('🔥 创建每周热榜'), callback_data="autowiz_start"),
            ]]),
        )
        return

    from telepost.domain.hot import active_timezone
    tz = active_timezone()

    lines = [tr("""⏰ 定时任务
""")]
    for i, t in enumerate(tasks, 1):
        status = tr('✅ 已启用') if t.enabled else tr('⛔ 已停用')
        last = "—" if not t.last_run_at else _fmt_ts(t.last_run_at)
        last_status = t.last_run_status or "—"
        next_run = t.next_run_at()
        next_str = "—" if not next_run else _fmt_ts(next_run)
        lines.append(
            tr("""{p0}. {p1}
   ⏰ {p2}  {p3}
   上次：{p4} {p5}
   下次：{p6}
""").format(p0=i, p1=t.action_label, p2=t.schedule_label, p3=status, p4=last, p5=last_status, p6=next_str)
        )
        # Buttons per task
    text = "\n".join(lines)

    keyboard = []
    for t in tasks:
        row = []
        if t.enabled:
            row.append(InlineKeyboardButton(tr('⏸ 停用'), callback_data=f"autod_{t.id}"))
        else:
            row.append(InlineKeyboardButton(tr('▶️ 启用'), callback_data=f"autoe_{t.id}"))
        row.append(InlineKeyboardButton(tr('▶️ 执行'), callback_data=f"autorun_{t.id}"))
        row.append(InlineKeyboardButton(tr('🗑 删除'), callback_data=f"autodel_{t.id}"))
        keyboard.append(row)

    await update.effective_message.reply_text(
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
        await update.effective_message.reply_text(f"⚠️ {e}")
        return

    next_run = task.next_run_at()
    preview = (
        tr("""⏰ 新任务预览

任务：{p0}
执行：{p1}
目标：审核群
时区：{p2}
下次：{p3}

确认创建？""").format(p0=task.action_label, p1=task.schedule_label, p2=task.timezone, p3=_fmt_ts(next_run) if next_run else '—')
    )
    # Store the pending task in user_data for the confirm callback
    context.user_data["pending_automation"] = task.to_json()
    await update.effective_message.reply_text(
        preview,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton(tr('✅ 确认创建'), callback_data="autoadd_confirm"),
            InlineKeyboardButton(tr('❌ 取消'), callback_data="autoadd_cancel"),
        ]]),
    )


async def _toggle_task(update: Update, context: ContextTypes.DEFAULT_TYPE, args: list, *, enabled: bool) -> None:
    if not args or not args[0].isdigit():
        await update.effective_message.reply_text(tr('用法：/schedule {p0} <id>').format(p0='enable' if enabled else 'disable'))
        return
    task_id = int(args[0])
    ok = await automation_store.set_enabled(task_id, enabled)
    if not ok:
        await update.effective_message.reply_text(tr('❌ 任务 {p0} 不存在').format(p0=task_id))
        return
    await record_event("automation_toggle", task_id=task_id, enabled=enabled, actor_id=update.effective_user.id)
    await _sync(update, context)
    await update.effective_message.reply_text(tr('✅ 任务 {p0} {p1}').format(p0=task_id, p1='已启用' if enabled else '已停用'))


async def _delete_task(update: Update, context: ContextTypes.DEFAULT_TYPE, args: list) -> None:
    if not args or not args[0].isdigit():
        await update.effective_message.reply_text(tr('用法：/schedule delete <id>'))
        return
    task_id = int(args[0])
    ok = await automation_store.delete_task(task_id)
    if not ok:
        await update.effective_message.reply_text(tr('❌ 任务 {p0} 不存在').format(p0=task_id))
        return
    await record_event("automation_delete", task_id=task_id, actor_id=update.effective_user.id)
    await _sync(update, context)
    await update.effective_message.reply_text(tr('✅ 任务 {p0} 已删除').format(p0=task_id))


async def _run_task(update: Update, context: ContextTypes.DEFAULT_TYPE, args: list) -> None:
    if not args or not args[0].isdigit():
        await update.effective_message.reply_text(tr('用法：/schedule run <id>'))
        return
    task_id = int(args[0])
    task = await automation_store.get_task(task_id)
    if not task:
        await update.effective_message.reply_text(tr('❌ 任务 {p0} 不存在').format(p0=task_id))
        return
    from telepost.application.automation import manual_run
    status, error = await manual_run(task, context.bot)
    await record_event("automation_manual_run", task_id=task_id, status=status, error=error, actor_id=update.effective_user.id)
    if status == "success":
        await update.effective_message.reply_text(tr('✅ 任务 {p0} 已执行').format(p0=task_id))
    else:
        await update.effective_message.reply_text(tr('⚠️ 任务 {p0} 执行失败：{p1}').format(p0=task_id, p1=error or status))


async def _preview_task(update: Update, context: ContextTypes.DEFAULT_TYPE, args: list) -> None:
    if not args or not args[0].isdigit():
        await update.effective_message.reply_text(tr('用法：/schedule preview <id>'))
        return
    task_id = int(args[0])
    task = await automation_store.get_task(task_id)
    if not task:
        await update.effective_message.reply_text(tr('❌ 任务 {p0} 不存在').format(p0=task_id))
        return
    if task.action_type != ACTION_HOT:
        await update.effective_message.reply_text(tr('⚠️ 该任务类型暂不支持预览'))
        return
    payload = task.action_payload or {}
    from telepost.domain.hot import HotQuery
    from handlers.stats_handlers import build_hot_message
    hq = HotQuery(scope=payload.get("scope", "all"), limit=payload.get("limit", 10))
    text = await build_hot_message(hq)
    await update.effective_message.reply_text(text, parse_mode="HTML", disable_web_page_preview=True)


async def _sync(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Re-sync JobQueue after any task mutation."""
    from telepost.application.automation import sync_jobqueue
    app = context.application
    await sync_jobqueue(app)


# ─── Callback handlers ───


# ─── Wizard ───

WIZ_DAYS = [(tr('周一'),0),(tr('周二'),1),(tr('周三'),2),(tr('周四'),3),(tr('周五'),4),(tr('周六'),5),(tr('周日'),6)]
WIZ_TIMES = ["20:00","21:00","08:00","12:00"]
WIZ_LIMITS = [5,10,20]


async def handle_automation_wizard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    data = query.data or ""
    if not _is_admin(update):
        await query.answer(tr('⛔ 仅管理员'), show_alert=True)
        return
    ud = context.user_data
    wiz = ud.get("autowiz", {})

    if data == "autowiz_start":
        ud["autowiz"] = {}
        rows = []
        row = []
        for label, d in WIZ_DAYS:
            row.append(InlineKeyboardButton(label, callback_data=f"autowiz_day_{d}"))
            if len(row) == 4:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        await query.edit_message_text(tr('① 选择星期'), reply_markup=InlineKeyboardMarkup(rows))
    elif data.startswith("autowiz_day_"):
        day = int(data.replace("autowiz_day_", ""))
        wiz["day"] = day
        ud["autowiz"] = wiz
        day_label = next(l for l, d in WIZ_DAYS if d == day)
        rows = [[InlineKeyboardButton(t, callback_data=f"autowiz_time_{t}") for t in WIZ_TIMES]]
        rows.append([InlineKeyboardButton(tr('⌨️ 输入其他时间'), callback_data="autowiz_time_custom")])
        await query.edit_message_text(
            tr('② 选择时间（已选：{p0}）').format(p0=day_label),
            reply_markup=InlineKeyboardMarkup(rows),
        )
    elif data == "autowiz_time_custom":
        wiz["awaiting_time"] = True
        ud["autowiz"] = wiz
        await query.edit_message_text(tr('② 请直接发送时间，格式 HH:MM（如 20:30）'))
    elif data.startswith("autowiz_time_"):
        t = data.replace("autowiz_time_", "")
        try:
            from telepost.domain.hot import parse_schedule_time
            h, m = parse_schedule_time(t)
        except ValueError:
            await query.answer(tr('时间格式不对，请重选'), show_alert=True)
            return
        wiz["hour"] = h
        wiz["minute"] = m
        wiz.pop("awaiting_time", None)
        ud["autowiz"] = wiz
        rows = [[InlineKeyboardButton(f"TOP {n}", callback_data=f"autowiz_limit_{n}") for n in WIZ_LIMITS]]
        await query.edit_message_text(
            tr('③ 选择数量（已选：{p0}）').format(p0=t),
            reply_markup=InlineKeyboardMarkup(rows),
        )
    elif data.startswith("autowiz_limit_"):
        n = int(data.replace("autowiz_limit_", ""))
        wiz["limit"] = n
        ud["autowiz"] = wiz
        day_label = next((l for l, d in WIZ_DAYS if d == wiz.get("day")), "?")
        hour, minute = wiz.get("hour", 20), wiz.get("minute", 0)
        preview = (
            tr("""⏰ 新任务预览

任务：每周热榜
执行：{p0} {p1:02d}:{p2:02d}
范围：本周
数量：TOP {p3}
目标：审核群

确认创建？""").format(p0=day_label, p1=hour, p2=minute, p3=n)
        )
        wiz["confirmed_payload"] = {
            "schedule_day": wiz.get("day", 6),
            "schedule_hour": hour,
            "schedule_minute": minute,
            "limit": n,
        }
        ud["autowiz"] = wiz
        await query.edit_message_text(
            preview,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(tr('✅ 创建'), callback_data="autowiz_confirm"),
                InlineKeyboardButton(tr('❌ 取消'), callback_data="autowiz_cancel"),
            ]]),
        )
    elif data == "autowiz_confirm":
        payload = wiz.get("confirmed_payload")
        if not payload:
            await query.answer(tr('会话已过期，请重新创建'), show_alert=True)
            return
        from config.settings import REVIEW_CHAT_ID, CHANNEL_ID
        target = str(REVIEW_CHAT_ID or CHANNEL_ID)
        from telepost.domain.automation import ACTION_HOT
        task = AutomationTask(
            id=None, name=tr('每周热榜'), enabled=True,
            schedule_type="weekly",
            schedule_day=payload.get("schedule_day", 6),
            schedule_hour=payload.get("schedule_hour", 20),
            schedule_minute=payload.get("schedule_minute", 0),
            action_type=ACTION_HOT,
            action_payload={"scope": "week", "limit": payload.get("limit", 10)},
            target_chat_id=target,
            timezone=active_timezone().key,
            created_by=update.effective_user.id,
        )
        created = await automation_store.create_task(task)
        await record_event("automation_create", task_id=created.id, actor_id=update.effective_user.id)
        ud.pop("autowiz", None)
        await _sync(update, context)
        next_run = created.next_run_at()
        next_str = _fmt_ts(next_run) if next_run else "—"
        await query.edit_message_text(
            tr("""✅ 任务已创建（#{p0}）
下次执行：{p1}""").format(p0=created.id, p1=next_str)
        )
    elif data == "autowiz_cancel":
        ud.pop("autowiz", None)
        await query.edit_message_text(tr('已取消'))

async def handle_automation_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    data = query.data or ""
    if not _is_admin(update):
        await query.answer(tr('⛔ 仅管理员'), show_alert=True)
        return

    if data == "autoadd_confirm":
        pending = context.user_data.get("pending_automation")
        if not pending:
            await query.answer(tr('预览已过期，请重新创建'), show_alert=True)
            return
        task = AutomationTask(**pending)
        created = await automation_store.create_task(task)
        context.user_data.pop("pending_automation", None)
        await record_event("automation_create", task_id=created.id, actor_id=update.effective_user.id)
        await _sync(update, context)
        await query.edit_message_text(
            tr("""✅ 任务已创建（#{p0}）
下次执行：{p1}""").format(p0=created.id, p1=_fmt_ts(created.next_run_at()))
        )
    elif data == "autoadd_cancel":
        context.user_data.pop("pending_automation", None)
        await query.edit_message_text(tr('已取消'))
    elif data.startswith("autoe_"):
        task_id = int(data.replace("autoe_", ""))
        await automation_store.set_enabled(task_id, True)
        await _sync(update, context)
        await query.answer(tr('✅ 已启用'))
        await query.edit_message_text(query.message.text + tr("""

✅ 已启用"""), parse_mode="HTML")
    elif data.startswith("autod_"):
        task_id = int(data.replace("autod_", ""))
        await automation_store.set_enabled(task_id, False)
        await _sync(update, context)
        await query.answer(tr('⏸ 已停用'))
    elif data.startswith("autorun_"):
        task_id = int(data.replace("autorun_", ""))
        task = await automation_store.get_task(task_id)
        if not task:
            await query.answer(tr('任务不存在'), show_alert=True)
            return
        from telepost.application.automation import manual_run
        status, error = await manual_run(task, context.bot)
        await record_event("automation_manual_run", task_id=task_id, status=status, error=error, actor_id=update.effective_user.id)
        await query.answer(tr('✅ 已执行') if status == "success" else f"⚠️ {error or status}", show_alert=(status != "success"))
    elif data.startswith("autodel_"):
        task_id = int(data.replace("autodel_", ""))
        ok = await automation_store.delete_task(task_id)
        await _sync(update, context)
        if ok:
            await record_event("automation_delete", task_id=task_id, actor_id=update.effective_user.id)
            await query.answer(tr('🗑 已删除'))
            await query.edit_message_text(query.message.text + tr("""

🗑 已删除"""), parse_mode="HTML")
        else:
            await query.answer(tr('任务不存在'), show_alert=True)


def _fmt_ts(ts) -> str:
    from datetime import datetime
    from telepost.domain.hot import active_timezone
    if not ts:
        return "—"
    return datetime.fromtimestamp(ts, tz=active_timezone()).strftime("%m-%d %H:%M")



