"""
投稿信息处理模块
处理标签、链接、标题、简介和剧透设置
"""
import logging
from datetime import datetime
from telegram import Update
from telegram.ext import ConversationHandler, CallbackContext

from models.state import STATE
from database.db_manager import get_db
from utils.helper_functions import validate_state, process_tags

logger = logging.getLogger(__name__)

@validate_state(STATE['TAG'])
async def handle_tag(update: Update, context: CallbackContext) -> int:
    """
    处理标签输入
    
    Args:
        update: Telegram 更新对象
        context: 回调上下文
        
    Returns:
        int: 下一个会话状态
    """
    logger.info(f"处理标签输入，user_id: {update.effective_user.id}")
    user_id = update.effective_user.id
    raw_tags = update.message.text.strip()
    success, processed_tags = process_tags(raw_tags)
    if not success or not processed_tags:
        await update.message.reply_text("❌ 标签格式错误，请重新输入（最多30个，用逗号分隔）")
        return STATE['TAG']
    try:
        async with get_db() as conn:
            c = await conn.cursor()
            await c.execute("UPDATE submissions SET tags=?, timestamp=? WHERE user_id=?",
                      (processed_tags, datetime.now().timestamp(), user_id))
        logger.info(f"标签保存成功，user_id: {user_id}")
    except Exception as e:
        logger.error(f"标签保存错误: {e}")
        await update.message.reply_text("❌ 标签保存失败，请稍后再试")
        return ConversationHandler.END
    await update.message.reply_text(
        "✅ 标签已保存，请发送链接（可选，如无需请回复\"无\"，需填写请以 http:// 或 https:// 开头，或发送 /skip_optional 跳过后续所有可选项）"
    )
    return STATE['LINK']

@validate_state(STATE['LINK'])
async def handle_link(update: Update, context: CallbackContext) -> int:
    """
    处理链接输入
    
    Args:
        update: Telegram 更新对象
        context: 回调上下文
        
    Returns:
        int: 下一个会话状态
    """
    logger.info(f"处理链接输入，user_id: {update.effective_user.id}")
    user_id = update.effective_user.id
    link = update.message.text.strip()
    if link.lower() == "无":
        link = ""
    elif not link.startswith(('http://', 'https://')):
        await update.message.reply_text("⚠️ 链接格式不正确，请以 http:// 或 https:// 开头，或回复\"无\"跳过")
        return STATE['LINK']
    try:
        async with get_db() as conn:
            c = await conn.cursor()
            await c.execute("UPDATE submissions SET link=?, timestamp=? WHERE user_id=?",
                      (link, datetime.now().timestamp(), user_id))
        logger.info(f"链接保存成功，user_id: {user_id}")
    except Exception as e:
        logger.error(f"链接保存错误: {e}")
        await update.message.reply_text("❌ 链接保存失败，请稍后再试")
        return ConversationHandler.END
    await update.message.reply_text("✅ 链接已保存，请发送标题（可选，如无需请回复\"无\"，或发送 /skip_optional 跳过后续所有可选项）")
    return STATE['TITLE']

@validate_state(STATE['TITLE'])
async def handle_title(update: Update, context: CallbackContext) -> int:
    """
    处理标题输入
    
    Args:
        update: Telegram 更新对象
        context: 回调上下文
        
    Returns:
        int: 下一个会话状态
    """
    logger.info(f"处理标题输入，user_id: {update.effective_user.id}")
    user_id = update.effective_user.id
    title = update.message.text.strip()
    title_to_store = "" if title.lower() == "无" else title[:100]
    try:
        async with get_db() as conn:
            c = await conn.cursor()
            await c.execute("UPDATE submissions SET title=?, timestamp=? WHERE user_id=?",
                      (title_to_store, datetime.now().timestamp(), user_id))
        logger.info(f"标题保存成功，user_id: {user_id}")
    except Exception as e:
        logger.error(f"标题保存错误: {e}")
        await update.message.reply_text("❌ 标题保存失败，请稍后再试")
        return ConversationHandler.END
    await update.message.reply_text("✅ 标题已保存，请发送简介（可选，如无需请回复\"无\"，或发送 /skip_optional 跳过后续所有可选项）")
    return STATE['NOTE']

@validate_state(STATE['NOTE'])
async def handle_note(update: Update, context: CallbackContext) -> int:
    """
    处理简介输入
    
    Args:
        update: Telegram 更新对象
        context: 回调上下文
        
    Returns:
        int: 下一个会话状态
    """
    logger.info(f"处理简介输入，user_id: {update.effective_user.id}")
    user_id = update.effective_user.id
    note = update.message.text.strip()
    note_to_store = "" if note.lower() == "无" else note[:600]
    try:
        async with get_db() as conn:
            c = await conn.cursor()
            await c.execute("UPDATE submissions SET note=?, timestamp=? WHERE user_id=?",
                      (note_to_store, datetime.now().timestamp(), user_id))
        logger.info(f"简介保存成功，user_id: {user_id}")
    except Exception as e:
        logger.error(f"简介保存错误: {e}")
        await update.message.reply_text("❌ 简介保存失败，请稍后再试")
        return ConversationHandler.END
    await update.message.reply_text("✅ 简介已保存")
    from handlers.preview_handlers import show_submission_preview
    return await show_submission_preview(update, context)

# 跳过可选项的处理函数
@validate_state(STATE['LINK'])
async def skip_optional_link(update: Update, context: CallbackContext) -> int:
    """
    跳过链接及后续可选项
    
    Args:
        update: Telegram 更新对象
        context: 回调上下文
        
    Returns:
        int: 下一个会话状态
    """
    logger.info(f"跳过链接、标题、简介，user_id: {update.effective_user.id}")
    user_id = update.effective_user.id
    try:
        async with get_db() as conn:
            c = await conn.cursor()
            await c.execute("UPDATE submissions SET link=?, title=?, note=?, timestamp=? WHERE user_id=?",
                      ("", "", "", datetime.now().timestamp(), user_id))
    except Exception as e:
        logger.error(f"/skip_optional 执行错误: {e}")
        await update.message.reply_text("❌ 跳过可选项失败，请稍后再试")
        return ConversationHandler.END
    await update.message.reply_text("✅ 链接、标题、简介已跳过")
    from handlers.preview_handlers import show_submission_preview
    return await show_submission_preview(update, context)

@validate_state(STATE['TITLE'])
async def skip_optional_title(update: Update, context: CallbackContext) -> int:
    """
    跳过标题及后续可选项
    
    Args:
        update: Telegram 更新对象
        context: 回调上下文
        
    Returns:
        int: 下一个会话状态
    """
    logger.info(f"跳过标题、简介，user_id: {update.effective_user.id}")
    user_id = update.effective_user.id
    try:
        async with get_db() as conn:
            c = await conn.cursor()
            await c.execute("UPDATE submissions SET title=?, note=?, timestamp=? WHERE user_id=?",
                      ("", "", datetime.now().timestamp(), user_id))
    except Exception as e:
        logger.error(f"/skip_optional 执行错误: {e}")
        await update.message.reply_text("❌ 跳过可选项失败，请稍后再试")
        return ConversationHandler.END
    await update.message.reply_text("✅ 标题、简介已跳过")
    from handlers.preview_handlers import show_submission_preview
    return await show_submission_preview(update, context)

@validate_state(STATE['NOTE'])
async def skip_optional_note(update: Update, context: CallbackContext) -> int:
    """
    跳过简介
    
    Args:
        update: Telegram 更新对象
        context: 回调上下文
        
    Returns:
        int: 下一个会话状态
    """
    logger.info(f"跳过简介，user_id: {update.effective_user.id}")
    user_id = update.effective_user.id
    try:
        async with get_db() as conn:
            c = await conn.cursor()
            await c.execute("UPDATE submissions SET note=?, timestamp=? WHERE user_id=?",
                      ("", datetime.now().timestamp(), user_id))
    except Exception as e:
        logger.error(f"/skip_optional 执行错误: {e}")
        await update.message.reply_text("❌ 跳过可选项失败，请稍后再试")
        return ConversationHandler.END
    await update.message.reply_text("✅ 简介已跳过")
    from handlers.preview_handlers import show_submission_preview
    return await show_submission_preview(update, context)