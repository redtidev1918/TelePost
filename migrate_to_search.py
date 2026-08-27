#!/usr/bin/env python3
"""
数据迁移脚本：将现有数据库中的帖子迁移到搜索引擎索引

使用方法：
    python migrate_to_search.py         # 迁移所有现有帖子
    python migrate_to_search.py --clear # 清空现有索引后重新迁移
"""
import asyncio
import argparse
import logging
from datetime import datetime

from database.db_manager import get_db
from utils.search_engine import init_search_engine, PostDocument, get_search_engine
from utils.logging_config import setup_logging

# 设置日志
setup_logging()
logger = logging.getLogger(__name__)


async def migrate_posts(clear_index: bool = False):
    """
    迁移现有数据库中的帖子到搜索引擎
    
    Args:
        clear_index: 是否清空现有索引
    """
    logger.info("开始迁移数据...")
    
    # 初始化搜索引擎
    # 从配置文件读取索引目录
    from config.settings import SEARCH_INDEX_DIR
    init_search_engine(index_dir=SEARCH_INDEX_DIR, from_scratch=clear_index)
    search_engine = get_search_engine()
    
    if clear_index:
        logger.info("已清空现有索引")
    
    # 获取所有已发布的帖子
    async with get_db() as conn:
        cursor = await conn.cursor()
        await cursor.execute("SELECT * FROM published_posts ORDER BY publish_time DESC")
        posts = await cursor.fetchall()
    
    if not posts:
        logger.info("数据库中没有帖子需要迁移")
        return
    
    logger.info(f"找到 {len(posts)} 篇帖子需要迁移")
    
    # 批量迁移
    migrated_count = 0
    error_count = 0
    
    with search_engine.ix.writer() as writer:
        for post in posts:
            try:
                # 创建搜索文档
                # 安全获取 filename 字段（可能不存在于旧数据）
                try:
                    filename = post['filename'] or ''
                except (KeyError, IndexError):
                    filename = ''
                
                post_doc = PostDocument(
                    message_id=post['message_id'],
                    title=post['title'] or '',
                    description=post['note'] or '',  # 使用 note 作为描述
                    tags=post['tags'] or '',
                    filename=filename,  # 文件名字段
                    link=post['link'] or '',
                    user_id=post['user_id'],
                    username=post['username'] or f"user{post['user_id']}",
                    publish_time=datetime.fromtimestamp(post['publish_time']),
                    views=post['views'] or 0,
                    heat_score=post['heat_score'] or 0
                )
                
                # 添加到索引（使用批量写入）
                search_engine.add_post(post_doc, writer=writer)
                migrated_count += 1
                
                if migrated_count % 100 == 0:
                    logger.info(f"已迁移 {migrated_count}/{len(posts)} 篇帖子...")
                    
            except Exception as e:
                error_count += 1
                logger.error(f"迁移帖子 {post['message_id']} 失败: {e}")
    
    # 显示统计信息
    logger.info("\n" + "="*60)
    logger.info("迁移完成！")
    logger.info(f"总帖子数: {len(posts)}")
    logger.info(f"成功迁移: {migrated_count}")
    logger.info(f"失败数量: {error_count}")
    logger.info("="*60)
    
    # 显示索引统计
    stats = search_engine.get_stats()
    logger.info(f"\n索引统计:")
    logger.info(f"  - 总文档数: {stats['total_docs']}")
    logger.info(f"  - 索引字段: {', '.join(stats['indexed_fields'])}")


def main():
    """主函数"""
    parser = argparse.ArgumentParser(description='迁移数据库到搜索引擎索引')
    parser.add_argument('--clear', action='store_true', 
                       help='清空现有索引后重新迁移（默认：增量迁移）')
    args = parser.parse_args()
    
    logger.info("="*60)
    logger.info("TelePost 数据迁移工具")
    logger.info("="*60)
    
    if args.clear:
        logger.warning("警告：将清空现有索引！")
        response = input("确定继续吗？(y/N): ")
        if response.lower() != 'y':
            logger.info("取消迁移")
            return
    
    # 运行异步迁移
    asyncio.run(migrate_posts(clear_index=args.clear))
    
    logger.info("\n迁移完成！现在可以启动机器人并使用新的搜索功能。")


if __name__ == '__main__':
    main()

