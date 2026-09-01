"""
搜索索引管理工具
提供索引重建、同步、优化等功能
"""
import logging
import argparse
import json
import asyncio
import aiosqlite
import os
import shutil
from datetime import datetime
from typing import Optional

from config.settings import DB_PATH
from utils.search_engine import get_search_engine, PostDocument

logger = logging.getLogger(__name__)


class IndexManager:
    """搜索索引管理器"""
    
    def __init__(self):
        self.search_engine = get_search_engine()
        if not self.search_engine:
            logger.error("搜索引擎未初始化")
    
    async def rebuild_index(self, clear_first: bool = True) -> dict:
        """
        重建搜索索引
        
        Args:
            clear_first: 是否先清空现有索引
            
        Returns:
            dict: 包含重建结果的字典 {success: bool, added: int, failed: int, errors: list}
        """
        if not self.search_engine:
            return {"success": False, "added": 0, "failed": 0, "errors": ["搜索引擎未初始化"]}
        
        result = {
            "success": True,
            "added": 0,
            "failed": 0,
            "errors": []
        }
        
        try:
            # 1. 清空现有索引（可选）
            if clear_first:
                logger.info("完全重建索引（删除并重新创建）...")
                # 关闭现有索引
                if hasattr(self.search_engine.ix, 'close'):
                    self.search_engine.ix.close()
                
                # 删除索引目录
                index_dir = self.search_engine.index_dir
                if os.path.exists(index_dir):
                    shutil.rmtree(index_dir)
                    logger.info(f"已删除索引目录: {index_dir}")
                
                # 重新初始化索引
                os.makedirs(index_dir, exist_ok=True)
                from whoosh.index import create_in
                # 使用模块级的 PostDocument，避免函数内重复导入造成作用域遮蔽
                self.search_engine.ix = create_in(index_dir, PostDocument.get_schema())
                logger.info(f"已重新创建索引: {index_dir}")
            
            # 2. 从数据库读取所有帖子
            logger.info("从数据库读取帖子...")
            conn = await aiosqlite.connect(DB_PATH)
            conn.row_factory = aiosqlite.Row
            
            cursor = await conn.execute('''
                SELECT message_id, user_id, username, title, tags, link, 
                       filename, caption, publish_time, views, heat_score
                FROM published_posts
                ORDER BY message_id
            ''')
            posts = await cursor.fetchall()
            await conn.close()
            
            logger.info(f"找到 {len(posts)} 个帖子")
            
            if not posts:
                logger.info("数据库中没有帖子，无需重建索引")
                return result
            
            # 3. 将帖子添加到索引
            logger.info("添加帖子到索引...")
            for post in posts:
                try:
                    # 转换时间戳为 datetime
                    publish_time = datetime.fromtimestamp(post['publish_time']) if post['publish_time'] else datetime.now()
                    
                    doc = PostDocument(
                        message_id=post['message_id'],
                        post_id=post['message_id'],  # 使用 message_id 作为 post_id
                        user_id=post['user_id'] or 0,
                        username=post['username'] or '',
                        title=post['title'] or '',
                        description=post['caption'] or '',  # 使用 caption 作为描述
                        tags=post['tags'] or '',
                        filename=post['filename'] or '',  # 文件名
                        link=post['link'] or '',
                        publish_time=publish_time,
                        views=post['views'] or 0,
                        heat_score=post['heat_score'] or 0.0
                    )
                    
                    self.search_engine.add_post(doc)
                    result["added"] += 1
                    logger.debug(f"已添加: message_id={post['message_id']}, 标题=\"{post['title'] or '(无)'}\"")
                    
                except Exception as e:
                    result["failed"] += 1
                    error_msg = f"添加文档失败 (message_id={post['message_id']}): {str(e)}"
                    result["errors"].append(error_msg)
                    logger.error(error_msg)
            
            logger.info(f"索引重建完成: 成功 {result['added']} 个, 失败 {result['failed']} 个")
            
            # 4. 验证索引
            with self.search_engine.ix.searcher() as searcher:
                doc_count = searcher.doc_count_all()
                logger.info(f"索引中的文档数: {doc_count}")
            
            result["success"] = result["failed"] == 0
            
        except Exception as e:
            result["success"] = False
            error_msg = f"重建索引时发生错误: {str(e)}"
            result["errors"].append(error_msg)
            logger.error(error_msg, exc_info=True)
        
        return result
    
    async def sync_index(self) -> dict:
        """
        同步索引：只添加数据库中存在但索引中不存在的帖子
        
        Returns:
            dict: 包含同步结果的字典 {success: bool, added: int, removed: int, errors: list}
        """
        if not self.search_engine:
            return {"success": False, "added": 0, "removed": 0, "errors": ["搜索引擎未初始化"]}
        
        result = {
            "success": True,
            "added": 0,
            "removed": 0,
            "errors": []
        }
        
        try:
            # 1. 获取数据库中的所有 message_id
            conn = await aiosqlite.connect(DB_PATH)
            conn.row_factory = aiosqlite.Row
            
            cursor = await conn.execute('SELECT message_id FROM published_posts WHERE is_deleted = 0')
            db_message_ids = {str(row['message_id']) for row in await cursor.fetchall()}
            
            # 2. 获取索引中的所有 message_id
            index_message_ids = set()
            with self.search_engine.ix.searcher() as searcher:
                from whoosh.query import Every
                results = searcher.search(Every(), limit=None)
                for hit in results:
                    index_message_ids.add(hit['message_id'])
            
            # 3. 找出需要添加的（在数据库但不在索引中）
            to_add = db_message_ids - index_message_ids
            
            # 4. 找出需要删除的（在索引但不在数据库中）
            to_remove = index_message_ids - db_message_ids
            
            logger.info(f"同步索引: 需要添加 {len(to_add)} 个, 需要删除 {len(to_remove)} 个")
            
            # 5. 添加缺失的帖子
            if to_add:
                for message_id in to_add:
                    try:
                        cursor = await conn.execute('''
                            SELECT message_id, user_id, username, title, tags, link, 
                                   filename, caption, publish_time, views, heat_score
                            FROM published_posts
                            WHERE message_id = ?
                        ''', (int(message_id),))
                        post = await cursor.fetchone()
                        
                        if post:
                            publish_time = datetime.fromtimestamp(post['publish_time']) if post['publish_time'] else datetime.now()
                            
                            doc = PostDocument(
                                message_id=post['message_id'],
                                post_id=post['message_id'],
                                user_id=post['user_id'] or 0,
                                username=post['username'] or '',
                                title=post['title'] or '',
                                description=post['caption'] or '',
                                tags=post['tags'] or '',
                                filename=post['filename'] or '',
                                link=post['link'] or '',
                                publish_time=publish_time,
                                views=post['views'] or 0,
                                heat_score=post['heat_score'] or 0.0
                            )
                            
                            self.search_engine.add_post(doc)
                            result["added"] += 1
                            logger.debug(f"已添加到索引: message_id={message_id}")
                    
                    except Exception as e:
                        error_str = str(e).lower()
                        # 如果是 Schema 不匹配错误，直接抛出异常触发重建
                        if "field" in error_str or "schema" in error_str:
                            raise
                        error_msg = f"添加文档失败 (message_id={message_id}): {str(e)}"
                        result["errors"].append(error_msg)
                        logger.error(error_msg)
            
            # 6. 删除多余的帖子
            if to_remove:
                for message_id in to_remove:
                    try:
                        self.search_engine.delete_post(int(message_id))
                        result["removed"] += 1
                        logger.debug(f"已从索引删除: message_id={message_id}")
                    
                    except Exception as e:
                        error_msg = f"删除文档失败 (message_id={message_id}): {str(e)}"
                        result["errors"].append(error_msg)
                        logger.error(error_msg)
            
            await conn.close()
            
            logger.info(f"索引同步完成: 添加 {result['added']} 个, 删除 {result['removed']} 个")
            
            result["success"] = len(result["errors"]) == 0
            
        except Exception as e:
            # 检查是否是 Schema 不匹配错误
            error_str = str(e).lower()
            if "field" in error_str or "schema" in error_str or "unknownfielderror" in error_str:
                logger.warning(f"同步索引时发生 Schema 错误: {str(e)}")
                # 抛出异常，让上层处理器触发重建
                raise
            
            result["success"] = False
            error_msg = f"同步索引时发生错误: {str(e)}"
            result["errors"].append(error_msg)
            logger.error(error_msg, exc_info=True)
        
        return result
    
    async def get_index_stats(self) -> dict:
        """
        获取索引统计信息
        
        Returns:
            dict: 索引统计信息
        """
        if not self.search_engine:
            return {"error": "搜索引擎未初始化"}
        
        try:
            # 数据库统计
            conn = await aiosqlite.connect(DB_PATH)
            cursor = await conn.execute('SELECT COUNT(*) FROM published_posts')
            db_count = (await cursor.fetchone())[0]
            await conn.close()
            
            # 索引统计
            with self.search_engine.ix.searcher() as searcher:
                index_count = searcher.doc_count_all()
            
            return {
                "db_count": db_count,
                "index_count": index_count,
                "in_sync": db_count == index_count,
                "difference": abs(db_count - index_count)
            }
        
        except Exception as e:
            logger.error(f"获取索引统计失败: {e}", exc_info=True)
            return {"error": str(e)}
    
    async def optimize_index(self) -> dict:
        """
        优化索引（合并段文件）
        
        Returns:
            dict: 优化结果，包含：
                - success: 是否成功
                - segments_before: 优化前段数（如有）
                - segments_after: 优化后段数（如有）
                - message: 结果消息
        """
        result = {
            "success": False,
            "message": ""
        }
        
        if not self.search_engine:
            result["message"] = "搜索引擎未初始化"
            logger.error(result["message"])
            return result
        
        try:
            logger.info("开始优化索引...")
            
            # 执行优化（合并索引段）
            writer = self.search_engine.ix.writer()
            writer.commit(optimize=True)
            
            result["success"] = True
            result["message"] = "索引优化完成"
            logger.info(result["message"])
            return result
        
        except Exception as e:
            result["message"] = f"优化索引失败: {str(e)}"
            logger.error(result["message"], exc_info=True)
            return result


# 全局索引管理器实例
_index_manager: Optional[IndexManager] = None


def get_index_manager() -> Optional[IndexManager]:
    """获取索引管理器实例（单例模式）"""
    global _index_manager
    if _index_manager is None:
        _index_manager = IndexManager()
    return _index_manager


async def auto_rebuild_index_if_needed() -> dict:
    """
    自动检查并重建索引（如果需要）
    在系统启动时调用
    
    Returns:
        dict: 操作结果
    """
    manager = get_index_manager()
    if not manager:
        return {"action": "none", "reason": "搜索引擎未初始化"}
    
    # 获取统计信息
    stats = await manager.get_index_stats()
    
    if "error" in stats:
        # 如果获取统计失败，可能是 Schema 不匹配，尝试重建索引
        error_msg = stats["error"]
        logger.warning(f"获取索引统计失败: {error_msg}")
        
        if "field" in error_msg.lower() or "schema" in error_msg.lower():
            logger.info("检测到 Schema 不匹配，尝试重建索引...")
            result = await manager.rebuild_index(clear_first=True)
            
            return {
                "action": "rebuild",
                "reason": "Schema 不匹配",
                "result": result
            }
        
        return {"action": "failed", "reason": error_msg}
    
    # 如果索引和数据库不同步，则同步
    if not stats["in_sync"]:
        logger.info(f"检测到索引不同步: 数据库 {stats['db_count']} 个, 索引 {stats['index_count']} 个")
        logger.info("开始同步索引...")
        
        try:
            result = await manager.sync_index()
            
            return {
                "action": "sync",
                "stats": stats,
                "result": result
            }
        except Exception as e:
            # 如果同步失败，可能是 Schema 问题，尝试完全重建
            error_str = str(e).lower()
            if "field" in error_str or "schema" in error_str:
                logger.warning(f"同步索引失败 (Schema 问题): {e}")
                logger.info("尝试重建索引...")
                result = await manager.rebuild_index(clear_first=True)
                
                return {
                    "action": "rebuild",
                    "reason": "Schema 不匹配导致同步失败",
                    "result": result
                }
            else:
                raise
    
    logger.info(f"索引已同步: {stats['index_count']} 个文档")
    return {
        "action": "none",
        "reason": "索引已同步",
        "stats": stats
    }


# -------------------------
# 简单命令行接口（CLI）
# -------------------------
def _print_json(data: dict):
    try:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    except Exception:
        print(data)


def _exit_code_from_result(result: dict) -> int:
    if not isinstance(result, dict):
        return 1
    # status 命令不以不同步为失败
    if "error" in result:
        return 1
    if "success" in result:
        return 0 if result.get("success") else 1
    return 0


def cli_main() -> int:
    parser = argparse.ArgumentParser(description="TelePost 索引管理工具")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # rebuild
    p_rebuild = subparsers.add_parser("rebuild", help="重建索引（可先清空）")
    p_rebuild.add_argument("--no-clear", action="store_true", help="不先清空索引，直接重建")

    # sync
    subparsers.add_parser("sync", help="同步索引（仅补齐缺失并清理多余）")

    # status
    subparsers.add_parser("status", help="查看索引与数据库统计")

    # optimize
    subparsers.add_parser("optimize", help="优化索引（合并段）")

    args = parser.parse_args()

    manager = get_index_manager()

    if args.command == "rebuild":
        result = asyncio.run(manager.rebuild_index(clear_first=not args.no_clear))
        _print_json(result)
        return _exit_code_from_result(result)

    if args.command == "sync":
        result = asyncio.run(manager.sync_index())
        _print_json(result)
        return _exit_code_from_result(result)

    if args.command == "status":
        result = asyncio.run(manager.get_index_stats())
        _print_json(result)
        return _exit_code_from_result(result)

    if args.command == "optimize":
        result = asyncio.run(manager.optimize_index())
        _print_json(result)
        return _exit_code_from_result(result)

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(cli_main())

