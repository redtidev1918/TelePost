"""
pytest 配置和公共 fixtures
"""
import os
import sys
import pytest
import tempfile
import shutil
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch

# 设置测试环境变量（在导入任何项目模块之前）
os.environ['TESTING'] = 'true'
os.environ['TOKEN'] = 'test_token_123456789'
os.environ['CHANNEL_ID'] = '@test_channel'
os.environ['OWNER_ID'] = '123456789'

# 添加项目根目录到 Python 路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


@pytest.fixture
def temp_dir():
    """创建临时目录"""
    temp_path = tempfile.mkdtemp()
    yield temp_path
    shutil.rmtree(temp_path)


@pytest.fixture
def sample_post_data():
    """示例投稿数据"""
    return {
        'message_id': 12345,
        'user_id': 123456789,
        'username': 'test_user',
        'content': '这是一条测试内容 #测试 #Python',
        'tags': '#测试 #Python',
        'created_at': '2024-01-01 10:00:00',
        'views': 100,
        'forwards': 10,
        'reactions': 5
    }


@pytest.fixture
def sample_stats():
    """示例统计数据"""
    return {
        'views': 1000,
        'forwards': 50,
        'reactions': 25
    }


@pytest.fixture
def mock_telegram_update():
    """模拟 Telegram Update 对象"""
    update = MagicMock()
    update.effective_user.id = 123456789
    update.effective_user.username = 'test_user'
    update.effective_user.first_name = 'Test'
    update.effective_chat.id = 987654321
    update.message.text = '/start'
    # 处理器普遍通过 effective_message / message 的异步 reply_text 回复
    update.message.reply_text = AsyncMock()
    update.effective_message.reply_text = AsyncMock()
    # 默认无回调来源；需要回调的测试显式覆盖
    update.callback_query = None
    return update


@pytest.fixture
def mock_telegram_context():
    """模拟 Telegram Context 对象"""
    context = MagicMock()
    context.bot = AsyncMock()
    context.user_data = {}
    context.chat_data = {}
    context.bot_data = {}
    return context


@pytest.fixture
def mock_database():
    """模拟数据库"""
    db = MagicMock()
    db.execute = AsyncMock(return_value=None)
    db.fetch_one = AsyncMock(return_value=None)
    db.fetch_all = AsyncMock(return_value=[])
    return db


@pytest.fixture(autouse=True)
def reset_environment():
    """重置环境变量"""
    # 保存原始环境变量
    original_env = os.environ.copy()
    
    # 设置测试环境变量
    os.environ['TESTING'] = 'true'
    
    yield
    
    # 恢复原始环境变量
    os.environ.clear()
    os.environ.update(original_env)


@pytest.fixture
def mock_config():
    """模拟配置"""
    return {
        'TOKEN': 'test_token',
        'CHANNEL_ID': '@test_channel',
        'OWNER_ID': 123456789,
        'DB_PATH': ':memory:',
        'ALLOWED_TAGS': 5,
        'SHOW_SUBMITTER': True,
        'NET_TIMEOUT': 30
    }
