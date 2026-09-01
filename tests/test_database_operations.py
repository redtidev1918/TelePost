"""
数据库操作测试模块
测试数据库的CRUD操作、事务、并发等
"""
import pytest
import sqlite3
import tempfile
import os
import time
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch


class TestSessionDatabaseOperations:
    """会话数据库操作测试"""
    
    @pytest.fixture
    def session_db(self, temp_dir):
        """创建临时会话数据库"""
        db_path = os.path.join(temp_dir, 'sessions.db')
        with patch('utils.database.SESSION_DB_PATH', db_path):
            from utils.database import initialize_database
            initialize_database()
            yield db_path
    
    @pytest.mark.database
    @pytest.mark.unit
    def test_initialize_database(self, temp_dir):
        """测试数据库初始化"""
        db_path = os.path.join(temp_dir, 'init_test.db')
        
        with patch('utils.database.SESSION_DB_PATH', db_path):
            from utils.database import initialize_database
            initialize_database()
            
            # 验证数据库文件存在
            assert os.path.exists(db_path)
            
            # 验证表结构
            conn = sqlite3.connect(db_path)
            cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = [row[0] for row in cursor.fetchall()]
            conn.close()
            
            assert 'user_sessions' in tables
    
    @pytest.mark.database
    @pytest.mark.unit
    def test_save_and_get_user_state(self, session_db):
        """测试保存和获取用户状态"""
        with patch('utils.database.SESSION_DB_PATH', session_db):
            from utils.database import save_user_state, get_user_state
            
            user_id = 12345
            state = "MEDIA"
            data = {"files": ["file1", "file2"]}
            
            save_user_state(user_id, state, data)
            
            result = get_user_state(user_id)
            
            assert result is not None
            assert result["state"] == state
            assert result["data"] == data
            assert "last_activity" in result
    
    @pytest.mark.database
    @pytest.mark.unit
    def test_update_user_activity(self, session_db):
        """测试更新用户活动时间"""
        with patch('utils.database.SESSION_DB_PATH', session_db):
            from utils.database import save_user_state, update_user_activity, get_user_state
            
            user_id = 12346
            save_user_state(user_id, "MEDIA", {})
            
            old_state = get_user_state(user_id)
            old_activity = old_state["last_activity"]
            
            time.sleep(0.1)
            update_user_activity(user_id)
            
            new_state = get_user_state(user_id)
            new_activity = new_state["last_activity"]
            
            assert new_activity > old_activity
    
    @pytest.mark.database
    @pytest.mark.unit
    def test_delete_user_state(self, session_db):
        """测试删除用户状态"""
        with patch('utils.database.SESSION_DB_PATH', session_db):
            from utils.database import save_user_state, delete_user_state, get_user_state
            
            user_id = 12347
            save_user_state(user_id, "MEDIA", {})
            
            # 确认存在
            assert get_user_state(user_id) is not None
            
            # 删除
            delete_user_state(user_id)
            
            # 确认已删除
            assert get_user_state(user_id) is None
    
    @pytest.mark.database
    @pytest.mark.unit
    def test_cleanup_expired_sessions(self, session_db):
        """测试清理过期会话"""
        with patch('utils.database.SESSION_DB_PATH', session_db):
            from utils.database import save_user_state, cleanup_expired_sessions, get_user_state
            
            user_id = 12348
            save_user_state(user_id, "MEDIA", {})
            
            # 立即清理（超时时间为0）
            cleanup_expired_sessions(timeout=0)
            
            # 会话应该被清理
            assert get_user_state(user_id) is None
    
    @pytest.mark.database
    @pytest.mark.unit
    def test_get_all_user_states(self, session_db):
        """测试获取所有用户状态"""
        with patch('utils.database.SESSION_DB_PATH', session_db):
            from utils.database import save_user_state, get_all_user_states
            
            # 保存多个用户状态
            for i in range(5):
                save_user_state(10000 + i, f"STATE_{i}", {"index": i})
            
            states = get_all_user_states()
            
            assert len(states) >= 5
    
    @pytest.mark.database
    @pytest.mark.unit
    def test_get_all_active_users(self, session_db):
        """测试获取所有活跃用户"""
        with patch('utils.database.SESSION_DB_PATH', session_db):
            from utils.database import save_user_state, get_all_active_users
            
            # 保存用户状态
            for i in range(3):
                save_user_state(20000 + i, "ACTIVE", {})
            
            users = get_all_active_users(timeout=3600)
            
            assert len(users) >= 3


class TestDatabaseErrorHandling:
    """数据库错误处理测试"""
    
    @pytest.mark.database
    @pytest.mark.unit
    def test_get_user_state_nonexistent(self, temp_dir):
        """测试获取不存在的用户状态"""
        db_path = os.path.join(temp_dir, 'error_test.db')
        
        with patch('utils.database.SESSION_DB_PATH', db_path):
            from utils.database import initialize_database, get_user_state
            initialize_database()
            
            result = get_user_state(99999999)
            assert result is None
    
    @pytest.mark.database
    @pytest.mark.unit
    def test_database_connection_error(self):
        """测试数据库连接错误"""
        with patch('utils.database.SESSION_DB_PATH', '/nonexistent/path/db.sqlite'):
            from utils.database import get_user_state
            
            result = get_user_state(12345)
            assert result is None
    
    @pytest.mark.database
    @pytest.mark.unit
    def test_corrupted_json_data(self, temp_dir):
        """测试损坏的JSON数据"""
        db_path = os.path.join(temp_dir, 'corrupt_json.db')
        
        # 创建数据库并插入损坏的JSON
        conn = sqlite3.connect(db_path)
        conn.execute('''
            CREATE TABLE user_sessions (
                user_id INTEGER PRIMARY KEY,
                state TEXT,
                data TEXT,
                last_activity REAL
            )
        ''')
        conn.execute('''
            INSERT INTO user_sessions VALUES (?, ?, ?, ?)
        ''', (12345, "STATE", "{invalid json}", time.time()))
        conn.commit()
        conn.close()
        
        with patch('utils.database.SESSION_DB_PATH', db_path):
            from utils.database import get_user_state
            
            # 应该优雅地处理错误
            result = get_user_state(12345)
            # 可能返回None或空数据
            assert result is None or result.get("data") == {}


class TestAsyncDatabaseOperations:
    """异步数据库操作测试"""
    
    @pytest.mark.database
    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_async_db_context_manager(self, temp_dir):
        """测试异步数据库上下文管理器"""
        db_path = os.path.join(temp_dir, 'async_test.db')
        
        with patch('database.db_manager.DB_PATH', db_path):
            from database.db_manager import get_db, init_db
            
            await init_db()
            
            async with get_db() as conn:
                # 执行简单查询
                cursor = await conn.execute("SELECT 1")
                result = await cursor.fetchone()
                assert result[0] == 1
    
    @pytest.mark.database
    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_async_db_transaction_rollback(self, temp_dir):
        """测试异步数据库事务回滚"""
        db_path = os.path.join(temp_dir, 'rollback_test.db')
        
        with patch('database.db_manager.DB_PATH', db_path):
            from database.db_manager import get_db, init_db
            
            await init_db()
            
            try:
                async with get_db() as conn:
                    await conn.execute("INSERT INTO submissions (user_id, timestamp) VALUES (?, ?)",
                                      (99999, time.time()))
                    # 故意引发错误
                    raise ValueError("Test error")
            except ValueError:
                pass
            
            # 验证事务被回滚
            async with get_db() as conn:
                cursor = await conn.execute("SELECT * FROM submissions WHERE user_id = ?", (99999,))
                result = await cursor.fetchone()
                assert result is None

    @pytest.mark.database
    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_deleted_post_syncs_review_status_and_backfills(self, temp_dir):
        """频道软删除后审核记录应为 deleted，既要覆盖新删除，也要迁移旧错位数据。"""
        db_path = os.path.join(temp_dir, 'review_delete_sync.db')

        with patch('database.db_manager.DB_PATH', db_path):
            from database.db_manager import get_db, init_db

            await init_db()
            now = time.time()
            async with get_db() as conn:
                await conn.executemany(
                    """
                    INSERT INTO pending_reviews (
                        idempotency_key, status, user_id, review_chat_id,
                        created_at, updated_at, published_message_id
                    ) VALUES (?, 'published', 7, '-100123', ?, ?, ?)
                    """,
                    [
                        ('triggered', now, now, 101),
                        ('backfill', now, now, 102),
                    ],
                )
                await conn.executemany(
                    """
                    INSERT INTO published_posts (message_id, publish_time, is_deleted)
                    VALUES (?, ?, ?)
                    """,
                    [(101, now, 0), (102, now, 1)],
                )
                await conn.execute(
                    "UPDATE published_posts SET is_deleted=1 WHERE message_id=101"
                )

            async with get_db() as conn:
                rows = await (await conn.execute(
                    "SELECT idempotency_key, status FROM pending_reviews ORDER BY id"
                )).fetchall()
            assert [(row['idempotency_key'], row['status']) for row in rows] == [
                ('triggered', 'deleted'),
                ('backfill', 'published'),
            ]

            await init_db()
            async with get_db() as conn:
                rows = await (await conn.execute(
                    "SELECT idempotency_key, status FROM pending_reviews ORDER BY id"
                )).fetchall()
            assert [(row['idempotency_key'], row['status']) for row in rows] == [
                ('triggered', 'deleted'),
                ('backfill', 'deleted'),
            ]


class TestDatabaseConcurrency:
    """数据库并发测试"""
    
    @pytest.mark.database
    @pytest.mark.unit
    def test_concurrent_writes(self, temp_dir):
        """测试并发写入"""
        db_path = os.path.join(temp_dir, 'concurrent.db')
        
        conn = sqlite3.connect(db_path)
        conn.execute('''
            CREATE TABLE test (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                value TEXT
            )
        ''')
        conn.commit()
        conn.close()
        
        import threading
        errors = []
        
        def write_data(thread_id):
            try:
                conn = sqlite3.connect(db_path, timeout=30)
                for i in range(20):
                    conn.execute("INSERT INTO test (value) VALUES (?)", (f"t{thread_id}_{i}",))
                    conn.commit()
                conn.close()
            except Exception as e:
                errors.append(e)
        
        threads = [threading.Thread(target=write_data, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        
        assert len(errors) == 0
        
        # 验证所有数据都被写入
        conn = sqlite3.connect(db_path)
        cursor = conn.execute("SELECT COUNT(*) FROM test")
        count = cursor.fetchone()[0]
        conn.close()
        
        assert count == 100  # 5 threads * 20 writes
    
    @pytest.mark.database
    @pytest.mark.unit
    def test_concurrent_reads_writes(self, temp_dir):
        """测试并发读写"""
        db_path = os.path.join(temp_dir, 'rw_concurrent.db')
        
        conn = sqlite3.connect(db_path)
        conn.execute('''
            CREATE TABLE test (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                value TEXT
            )
        ''')
        # 预先插入一些数据
        for i in range(10):
            conn.execute("INSERT INTO test (value) VALUES (?)", (f"initial_{i}",))
        conn.commit()
        conn.close()
        
        import threading
        errors = []
        
        def read_data():
            try:
                conn = sqlite3.connect(db_path, timeout=30)
                for _ in range(50):
                    cursor = conn.execute("SELECT * FROM test")
                    cursor.fetchall()
                conn.close()
            except Exception as e:
                errors.append(e)
        
        def write_data():
            try:
                conn = sqlite3.connect(db_path, timeout=30)
                for i in range(10):
                    conn.execute("INSERT INTO test (value) VALUES (?)", (f"new_{i}",))
                    conn.commit()
                conn.close()
            except Exception as e:
                errors.append(e)
        
        threads = []
        for i in range(3):
            threads.append(threading.Thread(target=read_data))
            threads.append(threading.Thread(target=write_data))
        
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        
        assert len(errors) == 0


class TestDatabaseIntegrity:
    """数据库完整性测试"""
    
    @pytest.mark.database
    @pytest.mark.unit
    def test_foreign_key_constraints(self, temp_dir):
        """测试外键约束"""
        db_path = os.path.join(temp_dir, 'fk_test.db')
        
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute('''
            CREATE TABLE users (
                id INTEGER PRIMARY KEY
            )
        ''')
        conn.execute('''
            CREATE TABLE posts (
                id INTEGER PRIMARY KEY,
                user_id INTEGER REFERENCES users(id)
            )
        ''')
        conn.commit()
        
        # 插入有效数据
        conn.execute("INSERT INTO users VALUES (1)")
        conn.execute("INSERT INTO posts VALUES (1, 1)")
        conn.commit()
        
        conn.close()
    
    @pytest.mark.database
    @pytest.mark.unit
    def test_unique_constraints(self, temp_dir):
        """测试唯一约束"""
        db_path = os.path.join(temp_dir, 'unique_test.db')
        
        conn = sqlite3.connect(db_path)
        conn.execute('''
            CREATE TABLE test (
                id INTEGER PRIMARY KEY,
                unique_value TEXT UNIQUE
            )
        ''')
        conn.commit()
        
        conn.execute("INSERT INTO test VALUES (1, 'value1')")
        conn.commit()
        
        # 尝试插入重复值应该失败
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO test VALUES (2, 'value1')")
        
        conn.close()
    
    @pytest.mark.database
    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_cleanup_old_data(self, temp_dir, monkeypatch):
        """测试清理旧数据"""
        db_path = os.path.join(temp_dir, 'cleanup_test.db')
        
        with patch('database.db_manager.DB_PATH', db_path):
            from database.db_manager import init_db, get_db, cleanup_old_data
            
            await init_db()
            
            # 插入旧数据
            old_timestamp = time.time() - 10000  # 很久以前
            review_old_timestamp = time.time() - 2 * 86400
            async with get_db() as conn:
                await conn.execute(
                    "INSERT INTO submissions (user_id, timestamp) VALUES (?, ?)",
                    (99998, old_timestamp)
                )
                await conn.executemany(
                    """
                    INSERT INTO pending_reviews (
                        idempotency_key, status, user_id, review_chat_id,
                        created_at, updated_at
                    ) VALUES (?, ?, 7, '-100123', ?, ?)
                    """,
                    [
                        ("expired-old", "expired", review_old_timestamp, review_old_timestamp),
                        ("deleted-old", "deleted", review_old_timestamp, review_old_timestamp),
                        ("pending-old", "pending", review_old_timestamp, review_old_timestamp),
                    ],
                )

            monkeypatch.setenv("REVIEW_RETENTION_DAYS", "1")
            
            # 清理
            await cleanup_old_data()
            
            # 验证数据被清理
            async with get_db() as conn:
                cursor = await conn.execute(
                    "SELECT * FROM submissions WHERE user_id = ?", (99998,)
                )
                result = await cursor.fetchone()
                assert result is None
                cursor = await conn.execute(
                    "SELECT idempotency_key, status FROM pending_reviews ORDER BY id"
                )
                reviews = await cursor.fetchall()
                assert [(row["idempotency_key"], row["status"]) for row in reviews] == [
                    ("pending-old", "pending")
                ]

    @pytest.mark.database
    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_api_notification_claim_survives_restart(self, temp_dir):
        db_path = os.path.join(temp_dir, 'notification_claim.db')
        with patch('database.db_manager.DB_PATH', db_path):
            from database.db_manager import (
                claim_api_notification,
                init_db,
                mark_api_notification_sent,
                release_api_notification,
            )

            await init_db()
            assert await claim_api_notification(7, 'empty:2026-09-01') is True
            assert await claim_api_notification(7, 'empty:2026-09-01') is False
            await mark_api_notification_sent(7, 'empty:2026-09-01', 321)
            assert await claim_api_notification(7, 'empty:2026-09-01') is False

            assert await claim_api_notification(7, 'retryable') is True
            await release_api_notification(7, 'retryable')
            assert await claim_api_notification(7, 'retryable') is True


class TestDatabaseMigration:
    """数据库迁移测试"""
    
    @pytest.mark.database
    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_add_column_migration(self, temp_dir):
        """测试添加列迁移 - 验证ALTER TABLE添加列的逻辑"""
        db_path = os.path.join(temp_dir, 'migration_test.db')
        
        import aiosqlite
        
        # 创建一个简单的表，然后测试添加列
        async with aiosqlite.connect(db_path) as conn:
            await conn.execute('''
                CREATE TABLE test_migration (
                    id INTEGER PRIMARY KEY,
                    name TEXT
                )
            ''')
            await conn.commit()
            
            # 模拟迁移：添加新列
            try:
                await conn.execute('ALTER TABLE test_migration ADD COLUMN new_column INTEGER DEFAULT 0')
                await conn.commit()
            except Exception:
                pass  # 列已存在
            
            # 验证新列存在
            cursor = await conn.execute("PRAGMA table_info(test_migration)")
            columns = [row[1] for row in await cursor.fetchall()]
            
            assert 'new_column' in columns
            assert 'id' in columns
            assert 'name' in columns


class TestDatabasePerformance:
    """数据库性能测试"""
    
    @pytest.mark.database
    @pytest.mark.unit
    def test_bulk_insert_performance(self, temp_dir):
        """测试批量插入性能"""
        db_path = os.path.join(temp_dir, 'perf_test.db')
        
        conn = sqlite3.connect(db_path)
        conn.execute('''
            CREATE TABLE test (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                value TEXT
            )
        ''')
        conn.commit()
        
        start_time = time.time()
        
        # 批量插入1000条记录
        data = [(f"value_{i}",) for i in range(1000)]
        conn.executemany("INSERT INTO test (value) VALUES (?)", data)
        conn.commit()
        
        elapsed = time.time() - start_time
        
        conn.close()
        
        # 应该在合理时间内完成
        assert elapsed < 5.0  # 5秒内
    
    @pytest.mark.database
    @pytest.mark.unit
    def test_index_usage(self, temp_dir):
        """测试索引使用"""
        db_path = os.path.join(temp_dir, 'index_test.db')
        
        conn = sqlite3.connect(db_path)
        conn.execute('''
            CREATE TABLE test (
                id INTEGER PRIMARY KEY,
                indexed_col TEXT,
                non_indexed_col TEXT
            )
        ''')
        conn.execute("CREATE INDEX idx_test ON test(indexed_col)")
        
        # 插入测试数据
        for i in range(1000):
            conn.execute("INSERT INTO test VALUES (?, ?, ?)",
                        (i, f"indexed_{i}", f"non_indexed_{i}"))
        conn.commit()
        
        # 使用EXPLAIN QUERY PLAN验证索引使用
        cursor = conn.execute(
            "EXPLAIN QUERY PLAN SELECT * FROM test WHERE indexed_col = 'indexed_500'"
        )
        plan = cursor.fetchone()
        
        # 应该使用索引
        assert 'idx_test' in str(plan) or 'SEARCH' in str(plan)
        
        conn.close()
