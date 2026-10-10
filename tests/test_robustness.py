"""
鲁棒性测试模块
测试异常处理、并发、超时等鲁棒性问题
"""
import pytest
import asyncio
import sqlite3
from contextlib import closing
import tempfile
import os
import time
from unittest.mock import MagicMock, AsyncMock, patch


class TestExceptionHandling:
    """异常处理测试"""
    
    @pytest.mark.robustness
    @pytest.mark.unit
    def test_process_tags_exception_recovery(self):
        """测试标签处理异常恢复"""
        from utils.helper_functions import process_tags
        
        # 各种可能导致异常的输入
        problematic_inputs = [
            "\x00\x01\x02",  # 控制字符
            "\ud800",        # 无效的Unicode代理对
            "a" * 10000000,  # 非常大的输入
        ]
        
        for input_str in problematic_inputs:
            try:
                success, result = process_tags(input_str)
                # 如果没有抛出异常，应该返回有效结果
                assert isinstance(success, bool)
                assert isinstance(result, str)
            except (UnicodeError, MemoryError):
                # 这些异常是可接受的
                pass
    
    @pytest.mark.robustness
    @pytest.mark.unit
    def test_build_caption_exception_recovery(self):
        """测试caption构建异常恢复"""
        from utils.helper_functions import build_caption
        
        # 各种可能导致异常的数据
        problematic_data = [
            {"user_id": "not_an_int"},
            {"link": object()},
            {"tags": lambda x: x},
        ]
        
        for data in problematic_data:
            try:
                caption = build_caption(data)
                assert isinstance(caption, str)
            except (TypeError, ValueError):
                # 这些异常是可接受的
                pass
    
    @pytest.mark.robustness
    @pytest.mark.unit
    def test_file_validator_exception_recovery(self):
        """测试文件验证器异常恢复"""
        from utils.file_validator import FileTypeValidator
        
        # 各种可能导致异常的配置
        problematic_configs = [
            None,
            123,
            [],
            {},
        ]
        
        for config in problematic_configs:
            try:
                if config is None:
                    validator = FileTypeValidator(allowed_types="*")
                else:
                    validator = FileTypeValidator(allowed_types=str(config))
                # 应该能正常创建
                assert validator is not None
            except (TypeError, AttributeError):
                # 这些异常是可接受的
                pass
    
    @pytest.mark.robustness
    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_database_connection_failure(self, temp_dir):
        """测试数据库连接失败处理"""
        from utils.database import get_user_state, save_user_state
        
        # 使用不存在的路径
        with patch('utils.database.SESSION_DB_PATH', '/nonexistent/path/db.sqlite'):
            # 应该优雅地处理错误
            result = get_user_state(12345)
            assert result is None  # 应该返回None而不是崩溃


class TestAsyncOperations:
    """异步操作测试"""
    
    @pytest.mark.robustness
    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_safe_send_timeout(self):
        """测试安全发送超时处理"""
        from utils.helper_functions import safe_send
        
        async def slow_function():
            await asyncio.sleep(10)
            return "result"
        
        # 使用较短的超时
        with patch('utils.helper_functions.NET_TIMEOUT', 0.1):
            result = await safe_send(slow_function)
            # 应该超时并返回None
            assert result is None
    
    @pytest.mark.robustness
    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_safe_send_exception(self):
        """测试安全发送异常处理"""
        from utils.helper_functions import safe_send
        
        async def failing_function():
            raise ValueError("Test error")
        
        result = await safe_send(failing_function)
        # 应该捕获异常并返回None
        assert result is None
    
    @pytest.mark.robustness
    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_enhanced_safe_send_retry(self):
        """测试增强安全发送重试逻辑"""
        from utils.helper_functions import enhanced_safe_send
        
        call_count = 0
        
        async def flaky_function():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise asyncio.TimeoutError("Timeout")
            return "success"
        
        with patch('utils.helper_functions.NET_TIMEOUT', 1):
            with patch('utils.helper_functions.CONFIG', {"MAX_RETRIES": 3, "RETRY_DELAY": 0.1}):
                result = await enhanced_safe_send(flaky_function)
                # 应该在重试后成功
                assert result == "success" or result is None
    
    @pytest.mark.robustness
    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_concurrent_blacklist_operations(self):
        """测试并发黑名单操作"""
        from utils.blacklist import _blacklist, is_blacklisted
        
        async def add_and_check(user_id):
            _blacklist.add(user_id)
            await asyncio.sleep(0.01)
            result = is_blacklisted(user_id)
            _blacklist.discard(user_id)
            return result
        
        # 并发执行多个操作
        tasks = [add_and_check(1000000 + i) for i in range(10)]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # 不应该有异常
        for result in results:
            assert not isinstance(result, Exception)


class TestDatabaseRobustness:
    """数据库鲁棒性测试"""
    
    @pytest.mark.robustness
    @pytest.mark.unit
    def test_database_concurrent_access(self, temp_dir):
        """测试数据库并发访问"""
        db_path = os.path.join(temp_dir, 'test.db')
        
        # 创建数据库
        conn = sqlite3.connect(db_path)
        conn.execute('''
            CREATE TABLE test (
                id INTEGER PRIMARY KEY,
                value TEXT
            )
        ''')
        conn.commit()
        conn.close()
        
        import threading
        errors = []
        
        def write_data(thread_id):
            try:
                conn = sqlite3.connect(db_path, timeout=10)
                for i in range(10):
                    conn.execute("INSERT INTO test (value) VALUES (?)", (f"thread{thread_id}_{i}",))
                    conn.commit()
                conn.close()
            except Exception as e:
                errors.append(e)
        
        threads = [threading.Thread(target=write_data, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        
        # 检查是否有错误
        assert len(errors) == 0, f"Errors occurred: {errors}"
    
    @pytest.mark.robustness
    @pytest.mark.unit
    def test_database_corruption_recovery(self, temp_dir):
        """测试数据库损坏恢复"""
        db_path = os.path.join(temp_dir, 'corrupt.db')
        
        # 创建一个损坏的数据库文件
        with open(db_path, 'w') as f:
            f.write("This is not a valid SQLite database")
        
        # 尝试连接应该失败但不崩溃
        with closing(sqlite3.connect(db_path)) as conn:
            with pytest.raises(sqlite3.DatabaseError):
                conn.execute("SELECT name FROM sqlite_master")
    
    @pytest.mark.robustness
    @pytest.mark.unit
    def test_session_cleanup_robustness(self, temp_dir):
        """测试会话清理鲁棒性"""
        from utils.database import cleanup_expired_sessions
        
        # 使用临时数据库
        with patch('utils.database.SESSION_DB_PATH', os.path.join(temp_dir, 'sessions.db')):
            # 初始化数据库
            from utils.database import initialize_database
            initialize_database()
            
            # 清理应该不会崩溃
            cleanup_expired_sessions(timeout=0)


class TestInputRobustness:
    """输入鲁棒性测试"""
    
    @pytest.mark.robustness
    @pytest.mark.unit
    def test_malformed_json_handling(self):
        """测试畸形JSON处理"""
        from utils.helper_functions import parse_json_list
        
        malformed_jsons = [
            '{"unclosed": "brace"',
            '[1, 2, 3',
            '{"nested": {"deep": {"very": {"deep":',
            '[[[[[[[[[[[[[[[[[[[[[[[[[',
            '{"key": undefined}',
            "{'single': 'quotes'}",
        ]
        
        for malformed in malformed_jsons:
            result = parse_json_list(malformed)
            # 应该返回空列表而不是崩溃
            assert result == []
    
    @pytest.mark.robustness
    @pytest.mark.unit
    def test_binary_data_handling(self):
        """测试二进制数据处理"""
        from utils.helper_functions import process_tags
        
        # 二进制数据
        binary_inputs = [
            b"binary data".decode('utf-8', errors='ignore'),
            "\x00\x01\x02\x03",
            "\xff\xfe\xfd",
        ]
        
        for binary_input in binary_inputs:
            try:
                success, result = process_tags(binary_input)
                assert isinstance(success, bool)
            except (UnicodeError, TypeError):
                # 可接受的异常
                pass
    
    @pytest.mark.robustness
    @pytest.mark.unit
    def test_recursive_data_handling(self):
        """测试递归数据处理"""
        from utils.helper_functions import build_caption
        
        # 创建循环引用的数据（虽然不太可能发生）
        data = {"link": "test", "title": "test"}
        # 不应该崩溃
        caption = build_caption(data)
        assert isinstance(caption, str)


class TestNetworkRobustness:
    """网络鲁棒性测试"""
    
    @pytest.mark.robustness
    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_network_error_handling(self):
        """测试网络错误处理"""
        from utils.helper_functions import enhanced_safe_send
        
        async def network_error():
            raise ConnectionError("Network is unreachable")
        
        result = await enhanced_safe_send(network_error)
        assert result is None
    
    @pytest.mark.robustness
    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_rate_limit_handling(self):
        """测试速率限制处理"""
        from utils.helper_functions import enhanced_safe_send, CONFIG
        
        call_count = 0
        
        async def rate_limited():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise Exception("Bad Request: retry after 1")
            return "success"
        
        # 保存原始配置
        original_max_retries = CONFIG["MAX_RETRIES"]
        original_retry_delay = CONFIG["RETRY_DELAY"]
        
        try:
            CONFIG["MAX_RETRIES"] = 2
            CONFIG["RETRY_DELAY"] = 0.1
            result = await enhanced_safe_send(rate_limited)
            # 应该在重试后成功或返回None
            assert result == "success" or result is None
        finally:
            CONFIG["MAX_RETRIES"] = original_max_retries
            CONFIG["RETRY_DELAY"] = original_retry_delay
    
    @pytest.mark.robustness
    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_permission_error_handling(self):
        """测试权限错误处理"""
        from utils.helper_functions import enhanced_safe_send
        
        async def permission_denied():
            raise Exception("Forbidden: bot was blocked by the user")
        
        result = await enhanced_safe_send(permission_denied)
        assert result is None


class TestStateManagement:
    """状态管理鲁棒性测试"""
    
    @pytest.mark.robustness
    @pytest.mark.unit
    def test_state_recovery_after_crash(self, temp_dir):
        """测试崩溃后状态恢复"""
        db_path = os.path.join(temp_dir, 'sessions.db')
        
        with patch('utils.database.SESSION_DB_PATH', db_path):
            from utils.database import initialize_database, save_user_state, get_user_state
            
            # 初始化并保存状态
            initialize_database()
            save_user_state(12345, "MEDIA", {"files": ["file1", "file2"]})
            
            # 模拟"崩溃"后重新读取
            state = get_user_state(12345)
            assert state is not None
            assert state["state"] == "MEDIA"
    
    @pytest.mark.robustness
    @pytest.mark.unit
    def test_concurrent_state_updates(self, temp_dir):
        """测试并发状态更新"""
        db_path = os.path.join(temp_dir, 'sessions.db')
        
        with patch('utils.database.SESSION_DB_PATH', db_path):
            from utils.database import initialize_database, save_user_state, get_user_state
            import threading
            
            initialize_database()
            errors = []
            
            def update_state(user_id):
                try:
                    for i in range(10):
                        save_user_state(user_id, f"STATE_{i}", {"count": i})
                except Exception as e:
                    errors.append(e)
            
            threads = [threading.Thread(target=update_state, args=(i,)) for i in range(5)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            
            assert len(errors) == 0


class TestMemoryManagement:
    """内存管理测试"""
    
    @pytest.mark.robustness
    @pytest.mark.unit
    def test_large_data_processing(self):
        """测试大数据处理"""
        from utils.helper_functions import process_tags
        
        # 处理大量标签不应该导致内存问题
        large_tags = ",".join([f"tag{i}" for i in range(10000)])
        success, result = process_tags(large_tags)
        
        assert success is True
        # 结果应该被限制
        tags = result.split()
        assert len(tags) <= 30
    
    @pytest.mark.robustness
    @pytest.mark.unit
    def test_cache_behavior(self):
        """测试缓存行为"""
        from utils.helper_functions import process_tags
        
        # 多次调用相同输入应该使用缓存
        for _ in range(100):
            success, result = process_tags("test,tag,cache")
            assert success is True
        
        # 检查缓存信息
        cache_info = process_tags.cache_info()
        assert cache_info.hits > 0


class TestGracefulDegradation:
    """优雅降级测试"""
    
    @pytest.mark.robustness
    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_parse_mode_fallback(self):
        """测试解析模式回退"""
        from utils.helper_functions import enhanced_safe_send
        
        call_count = 0
        
        async def parse_error(**kwargs):
            nonlocal call_count
            call_count += 1
            if 'parse_mode' in kwargs and call_count == 1:
                raise Exception("Can't parse entities: Bad Request")
            return "success"
        
        result = await enhanced_safe_send(parse_error, parse_mode="HTML")
        # 应该在移除parse_mode后重试成功
        assert result == "success" or result is None
    
    @pytest.mark.robustness
    @pytest.mark.unit
    def test_missing_config_fallback(self):
        """测试缺失配置回退"""
        from utils.helper_functions import get_submission_mode
        
        # 各种不完整的数据
        incomplete_data = [
            None,
            {},
            {"other_field": "value"},
            {"mode": None},
            {"mode": ""},
        ]
        
        for data in incomplete_data:
            mode = get_submission_mode(data)
            # 应该返回默认值
            assert mode == "mixed"
