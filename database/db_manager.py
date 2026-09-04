"""
数据库管理模块
"""
import logging
import os
from datetime import datetime
from contextlib import asynccontextmanager
import aiosqlite

from config.settings import DB_PATH, TIMEOUT, DB_CACHE_KB

logger = logging.getLogger(__name__)

@asynccontextmanager
async def get_db():
    """
    数据库连接上下文管理器
    
    Yields:
        aiosqlite.Connection: 数据库连接对象
    """
    conn = await aiosqlite.connect(DB_PATH)
    conn.row_factory = aiosqlite.Row
    # 优化 SQLite 运行参数，降低 I/O 延迟
    try:
        await conn.execute("PRAGMA journal_mode=WAL;")
        await conn.execute("PRAGMA synchronous=NORMAL;")
        await conn.execute("PRAGMA temp_store=MEMORY;")
        # 通过负值设置 KB 为单位的 page cache 大小（默认为 4MB，可通过 DB_CACHE_KB 配置）
        await conn.execute(f"PRAGMA cache_size={-int(DB_CACHE_KB)};")
    except Exception:
        pass
    try:
        yield conn
        await conn.commit()
    except Exception as e:
        await conn.rollback()
        raise e
    finally:
        await conn.close()

async def init_db():
    """
    初始化数据库
    """
    try:
        async with get_db() as conn:
            # 临时投稿数据表
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS submissions (
                    user_id INTEGER PRIMARY KEY,
                    timestamp REAL,
                    mode TEXT,
                    image_id TEXT,
                    document_id TEXT,
                    tags TEXT,
                    link TEXT,
                    title TEXT,
                    note TEXT,
                    spoiler TEXT,
                    anonymous TEXT DEFAULT 'false',
                    username TEXT
                )
            ''')

            # 匿名投稿字段（存量表补列）
            try:
                await conn.execute("ALTER TABLE submissions ADD COLUMN anonymous TEXT DEFAULT 'false'")
                logger.info("已添加 anonymous 字段到 submissions 表")
            except Exception:
                pass  # 字段已存在

            # API 访问令牌（用于 /api/v1 自动化投稿，仅存哈希不存明文）
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS api_tokens (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    token_hash TEXT UNIQUE NOT NULL,
                    telegram_user_id INTEGER NOT NULL,
                    name TEXT DEFAULT '',
                    created_at REAL,
                    revoked INTEGER DEFAULT 0
                )
            ''')

            # API/聊天投稿审核队列。媒体先上传到私有审核群，此处只保存
            # Telegram file_id 与审核状态，避免 Fly 重启后丢失待审核文件。
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS pending_reviews (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    idempotency_key TEXT UNIQUE NOT NULL,
                    source TEXT NOT NULL DEFAULT 'api',
                    status TEXT NOT NULL DEFAULT 'pending',
                    user_id INTEGER NOT NULL,
                    username TEXT DEFAULT '',
                    title TEXT DEFAULT '',
                    tags TEXT DEFAULT '',
                    note TEXT DEFAULT '',
                    link TEXT DEFAULT '',
                    anonymous INTEGER DEFAULT 0,
                    spoiler INTEGER DEFAULT 0,
                    media_json TEXT NOT NULL DEFAULT '[]',
                    documents_json TEXT NOT NULL DEFAULT '[]',
                    review_chat_id TEXT NOT NULL,
                    review_message_ids TEXT NOT NULL DEFAULT '[]',
                    control_message_id INTEGER,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    decided_at REAL,
                    decided_by INTEGER,
                    published_message_id INTEGER,
                    error TEXT DEFAULT ''
                )
            ''')
            try:
                await conn.execute(
                    "ALTER TABLE pending_reviews "
                    "ADD COLUMN source TEXT NOT NULL DEFAULT 'api'"
                )
                logger.info("已添加 source 字段到 pending_reviews 表")
            except Exception:
                pass  # 字段已存在
            try:
                await conn.execute(
                    "ALTER TABLE pending_reviews "
                    "ADD COLUMN target_id TEXT NOT NULL DEFAULT ''"
                )
                logger.info("已添加 target_id 字段到 pending_reviews 表")
            except Exception:
                pass  # 字段已存在
            await conn.execute(
                'CREATE INDEX IF NOT EXISTS idx_pending_reviews_status_created '
                'ON pending_reviews(status, created_at DESC)'
            )

            # API 运维通知的持久幂等记录。不同 token 所绑定的用户可以复用同一业务键；
            # 同一用户在 Bot 重启后仍不会重复发送同一条通知。
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS api_notifications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_user_id INTEGER NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    message_id INTEGER,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    UNIQUE(telegram_user_id, idempotency_key)
                )
            ''')
            await conn.execute(
                'CREATE INDEX IF NOT EXISTS idx_api_notifications_updated '
                'ON api_notifications(updated_at)'
            )
            
            # 已发布帖子表（用于热度统计和搜索）
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS published_posts (
                    message_id INTEGER PRIMARY KEY,
                    user_id INTEGER,
                    username TEXT,
                    title TEXT,
                    tags TEXT,
                    link TEXT,
                    note TEXT,
                    content_type TEXT,
                    file_ids TEXT,
                    caption TEXT,
                    filename TEXT,
                    publish_time REAL,
                    views INTEGER DEFAULT 0,
                    forwards INTEGER DEFAULT 0,
                    reactions INTEGER DEFAULT 0,
                    heat_score REAL DEFAULT 0,
                    last_update REAL,
                    related_message_ids TEXT,
                    is_deleted INTEGER DEFAULT 0
                )
            ''')
            
            # 添加 is_deleted 字段（如果表已存在但没有该字段）
            try:
                await conn.execute('ALTER TABLE published_posts ADD COLUMN is_deleted INTEGER DEFAULT 0')
                logger.info("已添加 is_deleted 字段到 published_posts 表")
            except Exception:
                # 字段已存在，忽略错误
                pass
            
            # 创建索引以提升查询性能
            await conn.execute('CREATE INDEX IF NOT EXISTS idx_heat_score ON published_posts(heat_score DESC)')
            await conn.execute('CREATE INDEX IF NOT EXISTS idx_publish_time ON published_posts(publish_time DESC)')
            await conn.execute('CREATE INDEX IF NOT EXISTS idx_user_id ON published_posts(user_id)')
            await conn.execute('CREATE INDEX IF NOT EXISTS idx_tags ON published_posts(tags)')
            await conn.execute('CREATE INDEX IF NOT EXISTS idx_is_deleted ON published_posts(is_deleted)')

            # 组合索引：覆盖 /hot、/myposts 等高频查询的
            # "WHERE is_deleted = 0 [+ user_id] ORDER BY ..." 热路径，
            # 避免在 is_deleted 单列索引上回表后再排序
            await conn.execute('CREATE INDEX IF NOT EXISTS idx_deleted_heat ON published_posts(is_deleted, heat_score DESC)')
            await conn.execute('CREATE INDEX IF NOT EXISTS idx_deleted_publish_time ON published_posts(is_deleted, publish_time DESC)')
            await conn.execute('CREATE INDEX IF NOT EXISTS idx_user_deleted ON published_posts(user_id, is_deleted)')

            # published_posts 是频道现状，pending_reviews 是审核审计。频道消息被软删除时
            # 同步把对应审核记录从“曾发布”推进到“已删除”，避免把历史终态误当成
            # 当前仍在线的发布。触发器覆盖项目内所有软删除入口。
            await conn.execute('''
                CREATE TRIGGER IF NOT EXISTS trg_published_post_review_deleted
                AFTER UPDATE OF is_deleted ON published_posts
                WHEN NEW.is_deleted = 1 AND OLD.is_deleted IS NOT 1
                BEGIN
                    UPDATE pending_reviews
                    SET status = 'deleted',
                        updated_at = CAST(strftime('%s', 'now') AS REAL),
                        error = CASE
                            WHEN COALESCE(error, '') = '' THEN 'published message deleted'
                            ELSE error
                        END
                    WHERE published_message_id = NEW.message_id
                      AND status = 'published';
                END
            ''')

            # 一次性修正触发器上线前已经出现的错位记录。保留 decided_at/decided_by，
            # 因为它们仍表示当时的批准操作；只更新当前生命周期状态。
            cursor = await conn.execute('''
                UPDATE pending_reviews
                SET status = 'deleted',
                    updated_at = CAST(strftime('%s', 'now') AS REAL),
                    error = CASE
                        WHEN COALESCE(error, '') = '' THEN 'published message deleted'
                        ELSE error
                    END
                WHERE status = 'published'
                  AND published_message_id IN (
                      SELECT message_id FROM published_posts WHERE is_deleted = 1
                  )
            ''')
            if cursor.rowcount:
                logger.info("已同步 %d 条已删除发布的审核状态", cursor.rowcount)
            
            await conn.commit()
            logger.info("数据库初始化完成")
    except Exception as e:
        logger.error(f"初始化数据库时出错: {e}")
        raise

async def cleanup_old_data():
    """
    清理过期的会话数据与已决审核记录
    """
    try:
        # 首先检查表是否存在
        async with aiosqlite.connect(DB_PATH) as conn:
            c = await conn.cursor()
            await c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='submissions'")
            table_exists = await c.fetchone()
            
        if not table_exists:
            logger.warning("submissions 表不存在，跳过清理")
            return
            
        # 如果表存在，执行清理
        async with get_db() as conn:
            c = await conn.cursor()
            cutoff = datetime.now().timestamp() - TIMEOUT
            await c.execute("DELETE FROM submissions WHERE timestamp < ?", (cutoff,))

        # 清理终态（拒绝/发布/已删除/失败/过期）且超过保留期的审核记录，避免表无限增长。
        # 只清理终态记录：pending 的投稿绝不能动。保留期默认 30 天。
        review_retention_days = int(os.getenv("REVIEW_RETENTION_DAYS", "30"))
        if review_retention_days > 0:
            async with aiosqlite.connect(DB_PATH) as conn:
                c = await conn.cursor()
                await c.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='pending_reviews'"
                )
                has_reviews = await c.fetchone()
            if has_reviews:
                review_cutoff = datetime.now().timestamp() - review_retention_days * 86400
                async with get_db() as conn:
                    c = await conn.cursor()
                    await c.execute(
                        "DELETE FROM pending_reviews "
                        "WHERE status IN ('rejected','published','deleted','failed','expired') AND updated_at < ?",
                        (review_cutoff,),
                    )
                    logger.info("已清理过期审核记录（保留 %d 天）", review_retention_days)

                async with get_db() as conn:
                    await conn.execute(
                        "DELETE FROM api_notifications WHERE updated_at < ?",
                        (review_cutoff,),
                    )
                    logger.info("已清理过期 API 通知幂等记录（保留 %d 天）", review_retention_days)
    except Exception as e:
        logger.error(f"清理过期数据失败: {e}")


async def claim_api_notification(telegram_user_id: int, idempotency_key: str) -> bool:
    """Atomically claim a notification key, surviving process restarts.

    A stale pending claim can be reclaimed after five minutes. This covers a
    process crash between the database claim and the Telegram request while
    suppressing ordinary concurrent duplicates.
    """
    now = datetime.now().timestamp()
    async with get_db() as conn:
        cursor = await conn.execute(
            """
            INSERT OR IGNORE INTO api_notifications (
                telegram_user_id, idempotency_key, status, created_at, updated_at
            ) VALUES (?, ?, 'pending', ?, ?)
            """,
            (telegram_user_id, idempotency_key, now, now),
        )
        if cursor.rowcount == 1:
            return True
        cursor = await conn.execute(
            """
            UPDATE api_notifications
            SET updated_at = ?
            WHERE telegram_user_id = ? AND idempotency_key = ?
              AND status = 'pending' AND updated_at < ?
            """,
            (now, telegram_user_id, idempotency_key, now - 300),
        )
        return cursor.rowcount == 1


async def mark_api_notification_sent(
    telegram_user_id: int,
    idempotency_key: str,
    message_id: int,
) -> None:
    async with get_db() as conn:
        await conn.execute(
            """
            UPDATE api_notifications
            SET status = 'sent', message_id = ?, updated_at = ?
            WHERE telegram_user_id = ? AND idempotency_key = ?
            """,
            (message_id, datetime.now().timestamp(), telegram_user_id, idempotency_key),
        )


async def release_api_notification(telegram_user_id: int, idempotency_key: str) -> None:
    """Release a failed pre-send claim so the caller's durable retry can proceed."""
    async with get_db() as conn:
        await conn.execute(
            """
            DELETE FROM api_notifications
            WHERE telegram_user_id = ? AND idempotency_key = ? AND status = 'pending'
            """,
            (telegram_user_id, idempotency_key),
        )
