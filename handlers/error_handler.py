"""
错误处理模块
"""
import logging
import traceback
from telegram import Update
from telegram.ext import CallbackContext
from telegram.error import BadRequest

logger = logging.getLogger(__name__)

async def error_handler(update: Update, context: CallbackContext) -> None:
    """
    处理程序错误的全局处理函数
    
    Args:
        update: Telegram 更新对象
        context: 回调上下文
    """
    # 提取错误信息
    error = context.error
    error_type = type(error).__name__
    error_msg = str(error)
    
    # 记录错误
    logger.error(f"处理更新时发生异常 - 类型:{error_type}, 消息:{error_msg}")
    
    # 记录用户信息（如果有）
    if update and update.effective_user:
        user_id = update.effective_user.id
        username = update.effective_user.username or "未设置"
        logger.error(f"错误涉及用户: 用户ID: {user_id}, 用户名: @{username}")
    
    # 记录更新信息（如果有）
    if update:
        # 检查是否是回调查询
        if update.callback_query:
            logger.error(f"错误发生在回调查询处理中，回调数据: {update.callback_query.data}")
            
            # 如果是回调查询中的NoneType错误，尝试恢复
            if error_type == "TypeError" and "NoneType" in error_msg and "await" in error_msg:
                logger.warning("检测到回调查询中的NoneType错误，尝试恢复...")
                try:
                    # 确认回调查询以防止界面阻塞
                    await update.callback_query.answer()
                    await update.effective_chat.send_message(
                        "操作已完成，请继续按照提示操作。如遇问题，请发送 /cancel 取消当前会话，然后重新开始。"
                    )
                    return
                except Exception as e:
                    logger.error(f"尝试恢复失败: {e}")
    
    # 对于一些已知类型的错误进行分类处理
    if "Unauthorized" in error_msg or "forbidden" in error_msg.lower() or "user is deactivated" in error_msg.lower():
        # 用户已阻止机器人
        logger.warning(f"用户已阻止机器人或无权访问: {error}")
        return
    
    elif isinstance(error, BadRequest):
        # Telegram API错误
        if "Message is not modified" in error_msg:
            # 消息未修改错误，可以忽略
            logger.debug("忽略消息未修改错误")
            return
        
        if "Query is too old" in error_msg:
            # 回调查询过期，可以忽略
            logger.debug("忽略回调查询过期错误")
            if update and update.callback_query:
                try:
                    await update.callback_query.answer("此操作已过期，请重新尝试")
                except:
                    pass
            return
    
    # 对于其他错误，尝试通知用户（如果可能）
    # 但不要向频道发送错误消息（bot 可能没有权限，且频道消息不应该触发用户交互）
    if update and update.effective_chat:
        # 排除频道消息
        if update.channel_post or update.edited_channel_post:
            logger.debug("错误发生在频道消息处理中，不发送错误通知")
            return
        
        # 检查是否是频道或群组
        if update.message and update.message.chat:
            chat_type = getattr(update.message.chat, 'type', None)
            if chat_type == 'channel':
                logger.debug("错误发生在频道中，不发送错误通知")
                return
        
        try:
            await update.effective_chat.send_message(
                "❌ 抱歉，处理您的请求时发生了错误。请稍后再试，或发送 /cancel 取消当前会话，然后发送 /start 重新开始。"
            )
        except Exception as e:
            logger.error(f"发送错误通知失败: {e}")
    
    # 记录详细的堆栈跟踪
    tb_list = traceback.format_exception(None, error, error.__traceback__)
    tb_string = ''.join(tb_list)
    logger.error(f"完整的堆栈跟踪:\n{tb_string}")
