#!/usr/bin/env python3
"""
统计功能诊断工具

帮助诊断为什么浏览量和热度显示为0
"""

import asyncio
import logging
from telegram import Bot
from config.settings import TOKEN, CHANNEL_ID
from database.db_manager import get_db

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


async def diagnose():
    """执行诊断"""
    
    print("=" * 70)
    print("📊 统计功能诊断工具")
    print("=" * 70)
    print()
    
    # 1. 检查配置
    print("1️⃣  检查配置...")
    print(f"   TOKEN: {'✅ 已配置' if TOKEN else '❌ 未配置'}")
    print(f"   CHANNEL_ID: {CHANNEL_ID if CHANNEL_ID else '❌ 未配置'}")
    print()
    
    # 2. 检查 Bot 权限
    print("2️⃣  检查 Bot 权限...")
    try:
        bot = Bot(TOKEN)
        
        # 获取频道信息
        chat = await bot.get_chat(CHANNEL_ID)
        print(f"   频道名称: {chat.title}")
        print(f"   频道类型: {chat.type}")
        
        # 获取 Bot 在频道的成员信息
        try:
            bot_member = await bot.get_chat_member(CHANNEL_ID, bot.id)
            print(f"   Bot 状态: {bot_member.status}")
            
            if bot_member.status == "administrator":
                print("   ✅ Bot 是频道管理员")
                
                # 检查具体权限
                if hasattr(bot_member, 'can_post_messages'):
                    print(f"   - 发送消息权限: {'✅' if bot_member.can_post_messages else '❌'}")
                if hasattr(bot_member, 'can_edit_messages'):
                    print(f"   - 编辑消息权限: {'✅' if bot_member.can_edit_messages else '❌'}")
                if hasattr(bot_member, 'can_delete_messages'):
                    print(f"   - 删除消息权限: {'✅' if bot_member.can_delete_messages else '❌'}")
            else:
                print(f"   ⚠️  Bot 不是管理员 (当前: {bot_member.status})")
                print("   统计功能可能受限")
        except Exception as e:
            print(f"   ❌ 无法获取 Bot 权限: {e}")
            print("   请确保 Bot 已被添加到频道")
        
    except Exception as e:
        print(f"   ❌ 连接失败: {e}")
    print()
    
    # 3. 检查数据库中的帖子
    print("3️⃣  检查数据库...")
    try:
        async with get_db() as conn:
            cursor = await conn.cursor()
            
            # 总帖子数
            await cursor.execute("SELECT COUNT(*) as count FROM published_posts")
            total_posts = (await cursor.fetchone())['count']
            print(f"   总帖子数: {total_posts}")
            
            # 有统计数据的帖子
            await cursor.execute("SELECT COUNT(*) as count FROM published_posts WHERE views > 0")
            posts_with_views = (await cursor.fetchone())['count']
            print(f"   有浏览量的帖子: {posts_with_views}")
            
            # 最近的帖子
            await cursor.execute("""
                SELECT message_id, views, forwards, heat_score, 
                       datetime(publish_time, 'unixepoch', 'localtime') as pub_time
                FROM published_posts 
                ORDER BY publish_time DESC 
                LIMIT 5
            """)
            recent_posts = await cursor.fetchall()
            
            if recent_posts:
                print(f"\n   最近 {len(recent_posts)} 个帖子:")
                for post in recent_posts:
                    print(f"   - 消息ID {post['message_id']}: "
                          f"👀 {post['views']} | 📤 {post['forwards']} | "
                          f"🔥 {post['heat_score']:.1f} | {post['pub_time']}")
            
    except Exception as e:
        print(f"   ❌ 数据库检查失败: {e}")
    print()
    
    # 4. 给出建议
    print("=" * 70)
    print("💡 诊断建议")
    print("=" * 70)
    print()
    
    if posts_with_views == 0 and total_posts > 0:
        print("❗ 所有帖子浏览量为 0")
        print("   Telegram Bot API 不支持无副作用回读任意频道帖统计。")
        print("   /hot 只展示数据库中已有的统计，不会转发频道消息探测数据。")
        print()


if __name__ == '__main__':
    try:
        asyncio.run(diagnose())
    except KeyboardInterrupt:
        print("\n\n已取消诊断")
    except Exception as e:
        logger.error(f"诊断过程出错: {e}", exc_info=True)
