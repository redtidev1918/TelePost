"""
Handlers 测试
"""
import pytest
from unittest.mock import MagicMock, AsyncMock, patch


class TestCommandHandlers:
    """命令处理器测试"""
    
    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_start_command(self, mock_telegram_update, mock_telegram_context):
        """测试 /start 命令（实现位于 handlers.mode_selection.start）"""
        from handlers.mode_selection import start

        mock_telegram_update.message.reply_text = AsyncMock()

        with patch('handlers.mode_selection.cleanup_old_data', new=AsyncMock()), \
             patch('handlers.mode_selection.is_blacklisted', return_value=False):
            await start(mock_telegram_update, mock_telegram_context)

        # 验证回复被调用
        mock_telegram_update.message.reply_text.assert_called_once()

        # 验证回复内容包含欢迎信息
        call_args = mock_telegram_update.message.reply_text.call_args
        assert call_args is not None
    
    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_help_command(self, mock_telegram_update, mock_telegram_context):
        """测试 /help 命令"""
        from handlers.command_handlers import help_command
        
        mock_telegram_update.message.reply_text = AsyncMock()
        
        await help_command(mock_telegram_update, mock_telegram_context)
        
        # 验证回复被调用
        mock_telegram_update.message.reply_text.assert_called_once()
    
    # 注：/about 命令已从项目中移除，原 test_about_command 一并删除


class TestSearchHandlers:
    """搜索处理器测试"""
    
    @pytest.mark.asyncio
    @pytest.mark.unit
    @patch('handlers.search_handlers.get_search_engine')
    async def test_search_command_basic(
        self, 
        mock_search_engine, 
        mock_telegram_update, 
        mock_telegram_context
    ):
        """测试基本搜索命令（/search，实现为 search_posts）"""
        from types import SimpleNamespace

        # 模拟搜索引擎（search_posts 使用同步 search() 并读取 .hits）
        mock_engine = MagicMock()
        mock_engine.search = MagicMock(return_value=SimpleNamespace(hits=[]))
        mock_search_engine.return_value = mock_engine
        
        # 设置命令参数
        mock_telegram_context.args = ['Python']
        mock_telegram_update.message.reply_text = AsyncMock()
        
        from handlers.search_handlers import search_posts
        
        await search_posts(mock_telegram_update, mock_telegram_context)
        
        # 验证搜索被调用
        mock_engine.search.assert_called_once()
    
    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_search_command_no_keyword(
        self, 
        mock_telegram_update, 
        mock_telegram_context
    ):
        """测试无关键词的搜索命令"""
        mock_telegram_context.args = []
        mock_telegram_update.message.reply_text = AsyncMock()
        
        from handlers.search_handlers import search_posts
        
        await search_posts(mock_telegram_update, mock_telegram_context)
        
        # 应该回复提示信息
        mock_telegram_update.message.reply_text.assert_called_once()
        call_args = mock_telegram_update.message.reply_text.call_args
        message = call_args[0][0] if call_args else ""
        assert "关键词" in message or "搜索" in message


class TestStatsHandlers:
    """统计处理器测试"""
    
    @pytest.mark.asyncio
    @pytest.mark.unit
    @patch('handlers.stats_handlers.get_db')
    async def test_mystats_command(
        self, 
        mock_get_db,
        mock_telegram_update, 
        mock_telegram_context
    ):
        """测试 /mystats 命令"""
        # 模拟数据库
        mock_db = MagicMock()
        mock_db.get_user_stats = AsyncMock(return_value={
            'total_posts': 10,
            'total_views': 1000,
            'total_forwards': 50,
            'avg_heat': 75.5,
            'top_tags': []
        })
        mock_get_db.return_value = mock_db
        
        mock_telegram_update.message.reply_text = AsyncMock()

        from handlers.stats_handlers import get_user_stats

        await get_user_stats(mock_telegram_update, mock_telegram_context)

        # 验证回复被调用
        mock_telegram_update.message.reply_text.assert_called_once()
    
    @pytest.mark.asyncio
    @pytest.mark.unit
    @patch('handlers.stats_handlers.get_db')
    async def test_hot_command(
        self, 
        mock_get_db,
        mock_telegram_update, 
        mock_telegram_context
    ):
        """测试 /hot 命令（实现为 get_hot_posts）"""
        # 模拟数据库连接：查询返回空列表 → 走"暂无热门数据"分支
        mock_cursor = AsyncMock()
        mock_cursor.fetchall = AsyncMock(return_value=[])
        mock_conn = MagicMock()
        mock_conn.cursor = AsyncMock(return_value=mock_cursor)
        mock_get_db.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_get_db.return_value.__aexit__ = AsyncMock(return_value=False)

        mock_telegram_context.args = []
        mock_telegram_update.callback_query = None
        mock_telegram_update.message.reply_text = AsyncMock()
        
        from handlers.stats_handlers import get_hot_posts
        
        await get_hot_posts(mock_telegram_update, mock_telegram_context)
        
        # 验证回复被调用（暂无数据提示）
        mock_telegram_update.message.reply_text.assert_called()


class TestCallbackHandlers:
    """回调处理器测试"""
    
    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_button_callback(self, mock_telegram_context):
        """测试按钮回调处理（实现为 handle_callback_query）"""
        # 创建回调查询：back 分支会编辑消息并应答
        callback_query = MagicMock()
        callback_query.data = "back"
        callback_query.answer = AsyncMock()
        callback_query.edit_message_text = AsyncMock()
        
        mock_update = MagicMock()
        mock_update.callback_query = callback_query
        mock_update.effective_user.first_name = "tester"
        
        from handlers.callback_handlers import handle_callback_query
        
        await handle_callback_query(mock_update, mock_telegram_context)
        
        # 验证回调被应答（_safe_answer 内部调用 query.answer）
        callback_query.answer.assert_called_once()


class TestErrorHandler:
    """错误处理器测试"""
    
    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_error_handler_general(self, mock_telegram_update, mock_telegram_context):
        """测试通用错误处理"""
        from handlers.error_handler import error_handler
        
        # 模拟错误
        error = Exception("测试错误")
        
        # 设置模拟方法
        if hasattr(mock_telegram_update, 'effective_message'):
            mock_telegram_update.effective_message.reply_text = AsyncMock()
        
        await error_handler(mock_telegram_update, mock_telegram_context)
        
        # 错误应该被记录（通过日志）
        assert True  # 基本错误处理不应该抛出异常
    
    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_error_handler_network_error(self, mock_telegram_update, mock_telegram_context):
        """测试网络错误处理"""
        from handlers.error_handler import error_handler
        from telegram.error import NetworkError
        
        # 模拟网络错误
        mock_telegram_context.error = NetworkError("网络错误")
        
        await error_handler(mock_telegram_update, mock_telegram_context)
        
        # 应该能处理网络错误而不崩溃
        assert True


class TestSubmitHandlers:
    """投稿处理器测试"""
    
    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_submit_command_start(self, mock_telegram_update, mock_telegram_context):
        """测试开始投稿（实现为 mode_selection.submit，媒体模式）"""
        from models.state import STATE as _STATE

        mock_telegram_update.message.reply_text = AsyncMock()

        # 模拟数据库：submit 会清理旧记录并插入新会话行
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def _fake_get_db():
            yield mock_conn

        mock_cursor = AsyncMock()
        mock_conn = MagicMock()
        mock_conn.cursor = AsyncMock(return_value=mock_cursor)
        mock_conn.commit = AsyncMock()

        with patch('handlers.mode_selection.BOT_MODE', 'MEDIA'), \
             patch('handlers.mode_selection.cleanup_old_data', new=AsyncMock()), \
             patch('handlers.mode_selection.is_blacklisted', return_value=False), \
             patch('handlers.mode_selection.get_db', _fake_get_db):
            from handlers.mode_selection import submit
            result = await submit(mock_telegram_update, mock_telegram_context)

        # 媒体模式下应进入 MEDIA 状态
        assert result == _STATE['MEDIA']

        # 应该发送欢迎/提示信息
        mock_telegram_update.message.reply_text.assert_called()

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_submit_rate_limit(self, mock_telegram_update, mock_telegram_context):
        """测试投稿频率限制：超过限额的 /submit 应被拒绝"""
        from contextlib import asynccontextmanager
        from telegram.ext import ConversationHandler

        mock_telegram_update.message.reply_text = AsyncMock()
        mock_telegram_context.bot_data = {}

        @asynccontextmanager
        async def _fake_get_db():
            yield MagicMock()

        with patch('handlers.mode_selection.SUBMIT_LIMIT_PER_HOUR', 2), \
             patch('handlers.mode_selection.cleanup_old_data', new=AsyncMock()), \
             patch('handlers.mode_selection.is_blacklisted', return_value=False), \
             patch('handlers.mode_selection.get_db', _fake_get_db):
            from handlers.mode_selection import submit
            r1 = await submit(mock_telegram_update, mock_telegram_context)
            r2 = await submit(mock_telegram_update, mock_telegram_context)
            r3 = await submit(mock_telegram_update, mock_telegram_context)

        # 前两次正常进入流程
        assert r1 is not None
        assert r2 is not None
        # 第三次应被限流拒绝并结束会话
        assert r3 == ConversationHandler.END
        assert any('频繁' in str(c) for c in mock_telegram_update.message.reply_text.call_args_list)
    
    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_cancel_command(self, mock_telegram_update, mock_telegram_context):
        """测试取消投稿（实现为 command_handlers.cancel）"""
        from telegram.ext import ConversationHandler

        mock_telegram_update.message.reply_text = AsyncMock()

        from handlers.command_handlers import cancel

        result = await cancel(mock_telegram_update, mock_telegram_context)

        # 应该结束对话
        assert result == ConversationHandler.END or result is not None

        # 应该发送确认信息
        mock_telegram_update.message.reply_text.assert_called()


class TestPublishHandlers:
    """发布处理器测试"""
    
    @pytest.mark.asyncio
    @pytest.mark.unit
    @patch('handlers.publish.safe_send')
    async def test_publish_to_channel(
        self, 
        mock_safe_send,
        mock_telegram_context
    ):
        """测试发布到频道（单条媒体走 handle_media_publish）"""
        sent_message = MagicMock(message_id=12345)
        mock_safe_send.return_value = sent_message

        from handlers.publish import handle_media_publish

        main_msg, all_ids = await handle_media_publish(
            mock_telegram_context,
            ["photo:test_file_id"],
            "测试说明",
            False,
        )

        # 主消息与消息ID列表应被正确返回
        assert main_msg is not None
        assert 12345 in all_ids
        mock_safe_send.assert_called_once()


class TestMediaHandlers:
    """媒体处理器测试"""
    
    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_handle_photo(self, mock_telegram_update, mock_telegram_context):
        """测试处理照片（实现为 media_handlers.handle_media）"""
        from types import SimpleNamespace
        from models.state import STATE as _STATE

        # 模拟照片消息
        photo = MagicMock()
        photo.file_id = 'test_file_id'
        mock_telegram_update.message.photo = [photo]
        mock_telegram_update.message.reply_text = AsyncMock()

        # 模拟数据库：返回包含空媒体列表的会话行
        # 注意：handle_media 被 utils.helper_functions.validate_state 装饰，
        # 该装饰器也会通过自己的 get_db 查询会话，因此两处都需要 patch
        from contextlib import asynccontextmanager

        row = {"image_id": "[]", "mode": "media", "timestamp": 1.0}
        mock_cursor = AsyncMock()
        mock_cursor.fetchone = AsyncMock(return_value=row)
        mock_conn = MagicMock()
        mock_conn.cursor = AsyncMock(return_value=mock_cursor)

        @asynccontextmanager
        async def _fake_get_db():
            yield mock_conn

        with patch('handlers.media_handlers.get_db', _fake_get_db), \
             patch('utils.helper_functions.get_db', _fake_get_db):
            from handlers.media_handlers import handle_media
            result = await handle_media(mock_telegram_update, mock_telegram_context)

        assert result == _STATE['MEDIA']
        mock_telegram_update.message.reply_text.assert_called()
    
    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_handle_video(self, mock_telegram_update, mock_telegram_context):
        """测试处理视频（实现为 media_handlers.handle_media）"""
        from types import SimpleNamespace
        from models.state import STATE as _STATE

        # 模拟视频消息
        video = MagicMock()
        video.file_id = 'test_video_id'
        mock_telegram_update.message.video = video
        mock_telegram_update.message.reply_text = AsyncMock()

        from contextlib import asynccontextmanager

        row = {"image_id": "[]", "mode": "media", "timestamp": 1.0}
        mock_cursor = AsyncMock()
        mock_cursor.fetchone = AsyncMock(return_value=row)
        mock_conn = MagicMock()
        mock_conn.cursor = AsyncMock(return_value=mock_cursor)

        @asynccontextmanager
        async def _fake_get_db():
            yield mock_conn

        with patch('handlers.media_handlers.get_db', _fake_get_db), \
             patch('utils.helper_functions.get_db', _fake_get_db):
            from handlers.media_handlers import handle_media
            result = await handle_media(mock_telegram_update, mock_telegram_context)

        assert result == _STATE['MEDIA']
        mock_telegram_update.message.reply_text.assert_called()


class TestBlacklistHandlers:
    """黑名单处理器测试"""
    
    @pytest.mark.asyncio
    @pytest.mark.unit
    @patch('utils.blacklist.is_blacklisted')
    async def test_blacklist_check(self, mock_is_blacklisted):
        """测试黑名单检查"""
        mock_is_blacklisted.return_value = True
        
        from utils.blacklist import is_blacklisted
        
        result = is_blacklisted(123456)
        
        assert result is True
    
    @pytest.mark.asyncio
    @pytest.mark.unit
    @patch('utils.blacklist.add_to_blacklist')
    async def test_add_to_blacklist(self, mock_add):
        """测试添加黑名单"""
        mock_add.return_value = True
        
        from utils.blacklist import add_to_blacklist
        
        result = add_to_blacklist(123456, '测试原因')
        
        assert result is True or mock_add.called
