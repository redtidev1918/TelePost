"""
媒体处理模块
"""
import json
import logging
from datetime import datetime
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ConversationHandler, CallbackContext

from models.state import STATE
from database.db_manager import get_db
from utils.helper_functions import (
    validate_state, end_conversation_with_message, handle_conversation_error,
    get_submission_mode, parse_json_list
)
from utils.file_validator import create_file_validator
from config.settings import ALLOWED_FILE_TYPES

logger = logging.getLogger(__name__)

@validate_state(STATE['MEDIA'])
async def handle_media(update: Update, context: CallbackContext) -> int:
    """
    处理媒体文件上传
    
    Args:
        update: Telegram 更新对象
        context: 回调上下文
        
    Returns:
        int: 当前会话状态
    """
    logger.info(f"处理媒体输入，user_id: {update.effective_user.id}")
    user_id = update.effective_user.id
    new_media = None

    if update.message.photo:
        file_id = update.message.photo[-1].file_id
        new_media = f"photo:{file_id}"
    elif update.message.video:
        file_id = update.message.video.file_id
        new_media = f"video:{file_id}"
    elif update.message.animation:
        file_id = update.message.animation.file_id
        new_media = f"animation:{file_id}"
    elif update.message.audio:
        file_id = update.message.audio.file_id
        new_media = f"audio:{file_id}"
    elif update.message.document:
        mime = update.message.document.mime_type
        logger.info(f"收到文档，MIME类型: {mime}, 用户ID: {user_id}")
        
        if mime == "image/gif":
            file_id = update.message.document.file_id
            new_media = f"animation:{file_id}"
        elif mime and mime.startswith("audio/"):
            file_id = update.message.document.file_id
            new_media = f"audio:{file_id}"
        else:
            # 检查是否是媒体模式
            try:
                async with get_db() as conn:
                    c = await conn.cursor()
                    await c.execute("SELECT mode FROM submissions WHERE user_id=?", (user_id,))
                    row = await c.fetchone()
                    mode = row["mode"] if row and "mode" in row.keys() else None
                    
                    logger.info(f"用户当前模式: {mode}, user_id: {user_id}")
                    
                    if row and mode == "media":
                        logger.info(f"用户在媒体模式下发送了文件附件，user_id: {user_id}, 文件名: {update.message.document.file_name}")
                        
                        # 创建切换到文档模式的内联键盘
                        keyboard = [
                            [InlineKeyboardButton("切换到文档模式", callback_data="switch_to_doc")]
                        ]
                        reply_markup = InlineKeyboardMarkup(keyboard)
                        
                        await update.message.reply_text(
                            "⚠️ 文件附件不能在媒体模式下上传。您可以：\n\n"
                            "1️⃣ 点击下方按钮切换到文档模式\n"
                            "2️⃣ 或发送 /cancel 取消当前投稿，然后发送 /start 重新选择文档模式",
                            reply_markup=reply_markup
                        )
                        return STATE['MEDIA']
            except Exception as e:
                logger.error(f"检查模式错误: {e}", exc_info=True)
            
            # 默认提示
            await update.message.reply_text(
                "⚠️ 不支持的文件类型，请发送支持的媒体\n\n"
                "📱 请直接发送媒体（非文件附件形式）：\n"
                "• 从相册选择后直接发送\n"
                "• 直接发送视频/GIF"
            )
            return STATE['MEDIA']
    else:
        await update.message.reply_text(
            "⚠️ 请发送支持的媒体文件\n\n"
            "📱 支持的媒体格式：图片、视频、GIF、音频"
        )
        return STATE['MEDIA']

    try:
        async with get_db() as conn:
            c = await conn.cursor()
            await c.execute("SELECT image_id, mode FROM submissions WHERE user_id=?", (user_id,))
            row = await c.fetchone()
            
            if not row:
                return await end_conversation_with_message(update, "❌ 会话已过期，请重新发送 /start")
                
            # 初始化媒体列表 - 使用统一的解析函数
            media_list = parse_json_list(row["image_id"])
            mode = get_submission_mode(row)
            
            # 根据模式设置不同的限制
            media_limit = 50 if mode == "media" else 10
            
            # 限制媒体数量
            if len(media_list) >= media_limit:
                await update.message.reply_text(f"⚠️ 已达到媒体上传上限（{media_limit}个）")
                return STATE['MEDIA']
                
            media_list.append(new_media)
            await c.execute("UPDATE submissions SET image_id=?, timestamp=? WHERE user_id=?",
                      (json.dumps(media_list), datetime.now().timestamp(), user_id))
            
            logger.info(f"当前媒体数量：{len(media_list)}")
            
            # 根据模式提供不同的提示
            if mode == "media":
                await update.message.reply_text(
                    f"✅ 已接收媒体，共计 {len(media_list)} 个。\n"
                    f"继续发送媒体文件，或发送 /done_media 完成上传。"
                )
            else:
                await update.message.reply_text(
                    f"✅ 已接收媒体，共计 {len(media_list)} 个。\n"
                    f"继续发送媒体文件，或发送 /done_media 完成上传，或发送 /skip_media 跳过该步骤。"
                )
                
    except Exception as e:
        logger.error(f"媒体保存错误: {e}")
        return await handle_conversation_error(update, "❌ 媒体保存失败，请稍后再试")
        
    return STATE['MEDIA']

@validate_state(STATE['MEDIA'])
async def done_media(update: Update, context: CallbackContext) -> int:
    """
    完成媒体上传，进入下一阶段
    
    Args:
        update: Telegram 更新对象
        context: 回调上下文
        
    Returns:
        int: 下一个会话状态
    """
    logger.info(f"媒体上传结束，user_id: {update.effective_user.id}")
    user_id = update.effective_user.id
    
    try:
        async with get_db() as conn:
            c = await conn.cursor()
            await c.execute("SELECT image_id, mode FROM submissions WHERE user_id=?", (user_id,))
            row = await c.fetchone()
            
            if not row:
                return await end_conversation_with_message(update, "❌ 会话已过期，请重新发送 /start")
            
            # 检查媒体文件是否存在，使用统一的解析函数
            media_list = parse_json_list(row["image_id"])
            mode = get_submission_mode(row)
            
            # 仅媒体模式下要求至少有一个媒体文件
            if mode == "media" and not media_list:
                await update.message.reply_text("⚠️ 请至少发送一个媒体文件")
                return STATE['MEDIA']
                
        # 媒体验证通过，进入标签阶段
        await update.message.reply_text("✅ 媒体接收完成，请发送标签（必选，最多30个，用逗号分隔，例如：明日方舟，原神）")
        return STATE['TAG']
        
    except Exception as e:
        logger.error(f"检索媒体错误: {e}")
        return await handle_conversation_error(update)

@validate_state(STATE['MEDIA'])
async def skip_media(update: Update, context: CallbackContext) -> int:
    """
    跳过媒体上传，进入下一阶段
    
    Args:
        update: Telegram 更新对象
        context: 回调上下文
        
    Returns:
        int: 下一个会话状态
    """
    logger.info(f"用户跳过媒体上传，user_id: {update.effective_user.id}")
    user_id = update.effective_user.id
    
    # 检查当前模式
    try:
        async with get_db() as conn:
            c = await conn.cursor()
            await c.execute("SELECT mode FROM submissions WHERE user_id=?", (user_id,))
            row = await c.fetchone()
            
            if not row:
                return await end_conversation_with_message(update, "❌ 会话已过期，请重新发送 /start")
                
            # 获取投稿模式
            mode = get_submission_mode(row)
            
            # 媒体模式下不允许跳过媒体上传
            if mode == "media":
                await update.message.reply_text("⚠️ 在媒体投稿模式下，媒体文件是必选项。请上传至少一个媒体文件。")
                return STATE['MEDIA']
                
        # 非媒体模式可以跳过
        await update.message.reply_text("✅ 已跳过媒体上传，请发送标签（必选，最多30个，用逗号分隔，例如：明日方舟，原神）")
        return STATE['TAG']
        
    except Exception as e:
        logger.error(f"检查模式错误: {e}")
        await update.message.reply_text("❌ 内部错误，请稍后再试")
        return ConversationHandler.END

async def prompt_media(update: Update, context: CallbackContext) -> int:
    """
    提示用户发送媒体文件
    
    Args:
        update: Telegram 更新对象
        context: 回调上下文
        
    Returns:
        int: 当前会话状态
    """
    # 检查当前模式
    user_id = update.effective_user.id
    try:
        async with get_db() as conn:
            c = await conn.cursor()
            await c.execute("SELECT mode FROM submissions WHERE user_id=?", (user_id,))
            row = await c.fetchone()
            
            if not row:
                return await end_conversation_with_message(update, "❌ 会话已过期，请重新发送 /start")
                
            # 获取投稿模式
            mode = get_submission_mode(row)
            
            # 根据模式提供不同的提示
            if mode == "media":
                await update.message.reply_text(
                    "请发送支持的媒体文件，或发送 /done_media 完成上传\n\n"
                    "📱 支持的媒体格式：图片、视频、GIF、音频"
                )
            else:
                await update.message.reply_text(
                    "请发送支持的媒体文件，或发送 /done_media 完成上传，或发送 /skip_media 跳过媒体上传\n\n"
                    "📱 支持的媒体格式：图片、视频、GIF、音频"
                )
                
    except Exception as e:
        logger.error(f"检查模式错误: {e}")
        # 默认提示
        await update.message.reply_text("请发送支持的媒体文件，或发送 /done_media 完成上传")
    
    return STATE['MEDIA']

# 添加处理切换到文档模式的回调函数
async def switch_to_doc_mode(update: Update, context: CallbackContext) -> int:
    """
    处理用户从媒体模式切换到文档模式的回调
    
    Args:
        update: Telegram 更新对象
        context: 回调上下文
        
    Returns:
        int: 下一个会话状态
    """
    query = update.callback_query
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    logger.info(f"用户请求从媒体模式切换到文档模式，user_id: {user_id}, data: {query.data}")
    
    # 先确认回调查询，这样用户界面会立即响应
    try:
        await query.answer()
    except Exception as e:
        logger.warning(f"确认回调查询失败，但将继续处理: {e}")
    
    try:
        # 1. 编辑原消息，告知用户正在切换
        try:
            await query.edit_message_text("✅ 正在切换到文档投稿模式...")
        except Exception as e:
            logger.warning(f"编辑消息失败，但将继续处理: {e}")
        
        # 2. 更新数据库
        async with get_db() as conn:
            c = await conn.cursor()
            
            # 更新用户模式为文档模式
            await c.execute("UPDATE submissions SET mode=?, image_id=?, document_id=? WHERE user_id=?", 
                            ("document", "[]", "[]", user_id))
            await conn.commit()
        
        # 3. 发送新的欢迎消息（简化版本）
        file_validator = create_file_validator(ALLOWED_FILE_TYPES)
        allowed_types_desc = file_validator.get_allowed_types_description()
        welcome_text = (
            "📮 欢迎使用文档投稿功能！请按照以下步骤提交：\n\n"
            "1️⃣ 发送文档文件（必选）：\n"
            "   - 至少上传1个文件，最多上传10个文件。\n"
            "   - 点击聊天输入框旁的📎图标选择文件\n"
            f"   - ✅ 允许的文件类型：\n{allowed_types_desc}\n"
            "   - 上传完毕后，发送 /done_doc\n\n"
            "2️⃣ 发送媒体文件（可选）：\n"
            "   - 支持图片、视频、GIF等，直接发送（非附件形式）\n"
            "   - 上传完毕后发送 /done_media，或发送 /skip_media 跳过\n\n"
            "3️⃣ 接下来按提示发送标签（必选）和其他可选信息\n\n"
            "随时可发送 /cancel 取消投稿"
        )
        
        await context.bot.send_message(chat_id=chat_id, text=welcome_text)
        logger.info(f"已成功切换到文档模式，user_id: {user_id}")

        # 通过返回值推进 ConversationHandler 状态。
        # 注意：不能使用 ApplicationHandlerStop —— 它只阻止后续处理器执行，
        # 并不会改变会话状态；会话状态只能由回调的返回值决定。
        return STATE['DOC']

    except Exception as e:
        logger.error(f"切换到文档模式错误: {e}", exc_info=True)
        try:
            # 尝试通知用户
            await context.bot.send_message(
                chat_id=chat_id, 
                text="❌ 切换模式失败，请发送 /cancel 取消当前会话，然后发送 /start 重新开始"
            )
        except Exception as send_error:
            logger.error(f"发送错误消息失败: {send_error}", exc_info=True)
        
        # 保持在当前状态
        return STATE['MEDIA']