"""
API 令牌管理命令（仅 OWNER 可生成，用户只能查看和吊销自己的 token）
"""
import html
import logging

from telegram import Update
from telegram.ext import CallbackContext, ConversationHandler

from utils import api_tokens
from utils.blacklist import is_owner

logger = logging.getLogger(__name__)


async def gen_token(update: Update, context: CallbackContext) -> int:
    """/gen_token <名称> —— 仅 OWNER 可生成 API token（明文仅显示一次）"""
    user_id = update.effective_user.id
    if not is_owner(user_id):
        logger.warning("非所有者用户 %s 尝试生成 API token", user_id)
        await update.message.reply_text("⛔ 此命令仅限机器人所有者使用")
        return ConversationHandler.END

    name = " ".join(context.args[:]) if context.args else ""
    if not name:
        await update.message.reply_text('用法：/gen_token <名称>，例如 /gen_token 我的脚本')
        return ConversationHandler.END

    token = await api_tokens.generate_token(user_id, name)
    logger.info(f"用户 {user_id} 生成 API token（名称: {name}）")
    await update.message.reply_text(
        "🔑 API token 已生成（仅显示这一次，请妥善保存）：\n\n"
        f"<code>{html.escape(token)}</code>\n\n"
        "用途与调用方式见项目文档 docs/API.md。\n"
        "可随时用 /tokens 查看、/revoke_token 吊销。",
        parse_mode="HTML",
    )
    return ConversationHandler.END


async def tokens(update: Update, context: CallbackContext) -> int:
    """/tokens —— 列出我的 API token"""
    user_id = update.effective_user.id
    rows = await api_tokens.list_tokens(user_id)
    if not rows:
        await update.message.reply_text("你还没有 API token，使用 /gen_token <名称> 生成。")
        return ConversationHandler.END

    lines = ["🔑 你的 API token："]
    for row in rows:
        status = "已吊销" if row["revoked"] else "有效"
        created = datetime.fromtimestamp(row["created_at"]).strftime("%Y-%m-%d") if row["created_at"] else "?"
        lines.append(f"• #{row['id']} {row['name']}（{created}，{status}）")
    lines.append("\n吊销：/revoke_token <编号>")
    await update.message.reply_text("\n".join(lines))
    return ConversationHandler.END


async def revoke_token(update: Update, context: CallbackContext) -> int:
    """/revoke_token <编号> —— 吊销 token"""
    user_id = update.effective_user.id
    if not context.args or not context.args[0].lstrip("#").isdigit():
        await update.message.reply_text("用法：/revoke_token <编号>（编号见 /tokens）")
        return ConversationHandler.END

    token_id = int(context.args[0].lstrip("#"))
    ok = await api_tokens.revoke_token(user_id, token_id)
    if ok:
        await update.message.reply_text(f"✅ token #{token_id} 已吊销")
    else:
        await update.message.reply_text("❌ 未找到该编号的 token（或不属于你）")
    return ConversationHandler.END
