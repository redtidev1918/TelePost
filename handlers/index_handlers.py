"""
搜索索引管理命令处理器
提供管理员手动管理索引的命令
"""
import logging
from telegram import Update
from telegram.ext import CallbackContext

from config.settings import ADMIN_IDS
from utils.index_manager import get_index_manager

logger = logging.getLogger(__name__)


async def rebuild_index_command(update: Update, context: CallbackContext) -> None:
    """
    /rebuild_index - 重建搜索索引
    仅管理员可用
    """
    user_id = update.effective_user.id
    
    # 检查管理员权限
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("❌ 此命令仅限管理员使用")
        return
    
    # 发送处理中消息
    status_msg = await update.message.reply_text("🔄 正在重建搜索索引，请稍候...")
    
    try:
        manager = get_index_manager()
        if not manager:
            await status_msg.edit_text("❌ 搜索引擎未初始化")
            return
        
        # 执行重建
        result = await manager.rebuild_index(clear_first=True)
        
        # 构建结果消息
        if result["success"]:
            message = (
                f"✅ 索引重建成功！\n\n"
                f"📊 统计信息:\n"
                f"  • 成功添加: {result['added']} 个文档\n"
                f"  • 失败: {result['failed']} 个文档"
            )
            
            if result["errors"]:
                message += f"\n\n⚠️ 错误:\n" + "\n".join(f"  • {err}" for err in result["errors"][:5])
        else:
            message = (
                f"❌ 索引重建失败\n\n"
                f"错误:\n" + "\n".join(f"  • {err}" for err in result["errors"][:5])
            )
        
        await status_msg.edit_text(message)
        logger.info(f"管理员 {user_id} 执行了索引重建")
        
    except Exception as e:
        logger.error(f"重建索引时发生错误: {e}", exc_info=True)
        await status_msg.edit_text(f"❌ 重建索引失败: {str(e)}")


async def sync_index_command(update: Update, context: CallbackContext) -> None:
    """
    /sync_index - 同步搜索索引
    仅管理员可用
    """
    user_id = update.effective_user.id
    
    # 检查管理员权限
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("❌ 此命令仅限管理员使用")
        return
    
    # 发送处理中消息
    status_msg = await update.message.reply_text("🔄 正在同步搜索索引，请稍候...")
    
    try:
        manager = get_index_manager()
        if not manager:
            await status_msg.edit_text("❌ 搜索引擎未初始化")
            return
        
        # 执行同步
        result = await manager.sync_index()
        
        # 构建结果消息
        if result["success"]:
            message = (
                f"✅ 索引同步成功！\n\n"
                f"📊 统计信息:\n"
                f"  • 新增: {result['added']} 个文档\n"
                f"  • 删除: {result['removed']} 个文档"
            )
            
            if result["errors"]:
                message += f"\n\n⚠️ 错误:\n" + "\n".join(f"  • {err}" for err in result["errors"][:5])
        else:
            message = (
                f"❌ 索引同步失败\n\n"
                f"错误:\n" + "\n".join(f"  • {err}" for err in result["errors"][:5])
            )
        
        await status_msg.edit_text(message)
        logger.info(f"管理员 {user_id} 执行了索引同步")
        
    except Exception as e:
        logger.error(f"同步索引时发生错误: {e}", exc_info=True)
        await status_msg.edit_text(f"❌ 同步索引失败: {str(e)}")


async def index_stats_command(update: Update, context: CallbackContext) -> None:
    """
    /index_stats - 查看索引统计信息
    仅管理员可用
    """
    user_id = update.effective_user.id
    
    # 检查管理员权限
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("❌ 此命令仅限管理员使用")
        return
    
    try:
        manager = get_index_manager()
        if not manager:
            await update.message.reply_text("❌ 搜索引擎未初始化")
            return
        
        # 获取统计信息
        stats = await manager.get_index_stats()
        
        if "error" in stats:
            await update.message.reply_text(f"❌ 获取统计信息失败: {stats['error']}")
            return
        
        # 构建统计消息
        sync_status = "✅ 已同步" if stats["in_sync"] else f"⚠️ 不同步 (差异: {stats['difference']})"
        
        message = (
            f"📊 搜索索引统计信息\n\n"
            f"数据库文档数: {stats['db_count']}\n"
            f"索引文档数: {stats['index_count']}\n"
            f"同步状态: {sync_status}\n\n"
        )
        
        if not stats["in_sync"]:
            message += "💡 提示: 使用 /sync_index 同步索引"
        
        await update.message.reply_text(message)
        logger.info(f"管理员 {user_id} 查看了索引统计")
        
    except Exception as e:
        logger.error(f"获取索引统计时发生错误: {e}", exc_info=True)
        await update.message.reply_text(f"❌ 获取统计信息失败: {str(e)}")


async def optimize_index_command(update: Update, context: CallbackContext) -> None:
    """
    /optimize_index - 优化搜索索引
    仅管理员可用
    """
    user_id = update.effective_user.id
    
    # 检查管理员权限
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("❌ 此命令仅限管理员使用")
        return
    
    # 发送处理中消息
    status_msg = await update.message.reply_text("🔄 正在优化搜索索引，请稍候...")
    
    try:
        manager = get_index_manager()
        if not manager:
            await status_msg.edit_text("❌ 搜索引擎未初始化")
            return
        
        # 执行优化
        # 注意：optimize_index 返回 {"success": bool, "message": str} 字典，
        # 非空字典恒为真值，必须取 success 字段判断
        result = await manager.optimize_index()

        if isinstance(result, dict):
            success = bool(result.get("success"))
            detail = result.get("message", "")
        else:
            success = bool(result)
            detail = ""

        if success:
            await status_msg.edit_text("✅ 索引优化成功！" + (f"\n{detail}" if detail else ""))
            logger.info(f"管理员 {user_id} 执行了索引优化")
        else:
            await status_msg.edit_text("❌ 索引优化失败" + (f"：{detail}" if detail else ""))
        
    except Exception as e:
        logger.error(f"优化索引时发生错误: {e}", exc_info=True)
        await status_msg.edit_text(f"❌ 优化索引失败: {str(e)}")

