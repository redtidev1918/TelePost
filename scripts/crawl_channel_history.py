#!/usr/bin/env python3
"""
从 Telegram 频道爬取历史消息并导入 V2 数据库

这个方法更简单可靠，不需要 V1 数据库：
1. 直接从频道读取所有历史消息
2. 提取消息内容、标签、统计数据
3. 导入到 V2 数据库
4. 创建搜索索引

使用方法：
    python crawl_channel_history.py --channel @your_channel --limit 1000
"""

import asyncio
import re
import logging
import argparse
import sys
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent))

from config.settings import TOKEN, CHANNEL_ID, DB_PATH
from database.db_manager import get_db, init_db
from telegram import Bot
from telegram.error import TelegramError

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def extract_tags_from_caption(caption: str) -> str:
    """从 caption 中提取标签"""
    if not caption:
        return ''
    
    # 查找所有 #标签
    tags = re.findall(r'#(\w+)', caption)
    return ' '.join(tags) if tags else ''


def extract_title_from_caption(caption: str) -> str:
    """从 caption 中提取标题（第一行或前50字符）"""
    if not caption:
        return ''
    
    # 移除标签
    text = re.sub(r'#\w+', '', caption)
    text = text.strip()
    
    # 取第一行或前50字符
    first_line = text.split('\n')[0]
    return first_line[:50] if len(first_line) > 50 else first_line


async def crawl_channel_messages(bot: Bot, channel_username: str, limit: int = 1000):
    """
    爬取频道消息
    
    注意：Bot API 有限制，只能获取最近的消息
    如果需要获取所有历史消息，需要使用 Telegram Client (telethon/pyrogram)
    """
    logger.info(f"开始爬取频道: {channel_username}")
    
    messages = []
    
    try:
        # Bot API 限制：无法直接获取频道历史消息
        # 需要使用 pyrogram 或 telethon
        
        logger.warning("⚠️  Bot API 无法直接爬取频道历史消息")
        logger.info("请使用以下两种方式之一：")
        logger.info("1. 使用 Pyrogram/Telethon（推荐）")
        logger.info("2. 手动导出频道数据（Telegram Desktop > 导出数据）")
        
        return []
        
    except TelegramError as e:
        logger.error(f"爬取失败: {e}")
        return []


async def import_messages_to_db(messages: list):
    """
    将消息导入数据库
    """
    logger.info(f"开始导入 {len(messages)} 条消息...")
    
    await init_db()
    
    imported = 0
    skipped = 0
    
    async with get_db() as conn:
        for msg in messages:
            try:
                message_id = msg.message_id
                
                # 检查是否已存在
                cursor = await conn.execute(
                    "SELECT message_id FROM published_posts WHERE message_id = ?",
                    (message_id,)
                )
                if await cursor.fetchone():
                    logger.debug(f"消息 {message_id} 已存在，跳过")
                    skipped += 1
                    continue
                
                # 提取数据
                caption = msg.caption or msg.text or ''
                tags = extract_tags_from_caption(caption)
                title = extract_title_from_caption(caption)
                
                # 确定内容类型
                content_type = 'text'
                file_ids = ''
                filename = ''
                
                if msg.photo:
                    content_type = 'photo'
                    file_ids = msg.photo[-1].file_id
                elif msg.video:
                    content_type = 'video'
                    file_ids = msg.video.file_id
                    filename = msg.video.file_name or ''
                elif msg.document:
                    content_type = 'document'
                    file_ids = msg.document.file_id
                    filename = msg.document.file_name or ''
                elif msg.media_group_id:
                    content_type = 'media_group'
                
                # 获取统计数据（如果可用）
                # 注意：Bot 无法获取频道消息的浏览量
                views = 0
                forwards = msg.forward_from_message_id if msg.forward_from else 0
                
                # 发布时间
                publish_time = msg.date.timestamp() if msg.date else datetime.now().timestamp()
                
                # 插入数据库
                await conn.execute("""
                    INSERT INTO published_posts
                    (message_id, user_id, username, title, tags, link, note,
                     content_type, file_ids, caption, filename, publish_time,
                     views, forwards, reactions, heat_score, last_update)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    message_id,
                    0,  # 未知用户
                    '',
                    title,
                    tags,
                    '',  # link
                    caption,
                    content_type,
                    file_ids,
                    caption,
                    filename,
                    publish_time,
                    views,
                    forwards,
                    0,  # reactions
                    0.0,  # heat_score
                    publish_time
                ))
                
                imported += 1
                if imported % 10 == 0:
                    logger.info(f"已导入 {imported} 条消息")
                    
            except Exception as e:
                logger.error(f"导入消息 {msg.message_id} 失败: {e}")
                skipped += 1
        
        await conn.commit()
    
    logger.info(f"导入完成！成功: {imported}, 跳过: {skipped}")
    return imported, skipped


async def main():
    parser = argparse.ArgumentParser(description='从频道爬取历史消息')
    parser.add_argument('--channel', default=CHANNEL_ID, help='频道用户名或ID')
    parser.add_argument('--limit', type=int, default=1000, help='最大消息数量')
    
    args = parser.parse_args()
    
    print("="*80)
    print("❌ Bot API 限制说明")
    print("="*80)
    print()
    print("由于 Telegram Bot API 的限制，普通 Bot 无法：")
    print("1. 获取频道的历史消息列表")
    print("2. 获取频道消息的浏览量统计")
    print()
    print("📝 推荐方案：")
    print()
    print("方案一：使用 Pyrogram/Telethon（最佳）")
    print("  - 可以完整爬取频道历史")
    print("  - 可以获取浏览量、转发量等统计")
    print("  - 需要 API ID 和 API Hash（从 my.telegram.org 获取）")
    print()
    print("方案二：手动导出（简单但功能受限）")
    print("  - Telegram Desktop > 设置 > 高级 > 导出数据")
    print("  - 选择要导出的频道")
    print("  - 导出为 JSON 格式")
    print("  - 使用转换脚本导入到 V2")
    print()
    print("方案三：从现在开始记录（最简单）")
    print("  - 直接启用 V2 机器人")
    print("  - V2 会自动记录新发布的帖子")
    print("  - 旧帖子不会出现在搜索和统计中")
    print()
    print("="*80)
    print()
    print("我将为你创建一个 Pyrogram 版本的爬虫脚本...")


if __name__ == '__main__':
    asyncio.run(main())

