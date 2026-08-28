"""
配置文件读取和变量定义模块
"""
import os
import configparser
import logging

logger = logging.getLogger(__name__)

# 项目根目录
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(BASE_DIR, 'config.ini')

# 读取配置文件
config = configparser.ConfigParser()

# 安全读取配置文件
if os.path.exists(CONFIG_PATH):
    config.read(CONFIG_PATH)
    logger.info(f"已加载配置文件: {CONFIG_PATH}")
else:
    logger.warning(f"⚠️ 配置文件 {CONFIG_PATH} 不存在，将仅使用环境变量")

# 辅助函数：安全获取配置
def get_config(section, key, fallback=None):
    """安全获取配置值"""
    try:
        return config.get(section, key)
    except (configparser.NoSectionError, configparser.NoOptionError, ValueError):
        return fallback

def get_config_int(section, key, fallback=0):
    """安全获取整数配置值"""
    try:
        return config.getint(section, key)
    except (configparser.NoSectionError, configparser.NoOptionError, ValueError):
        return fallback

def get_config_bool(section, key, fallback=False):
    """安全获取布尔配置值"""
    try:
        return config.getboolean(section, key)
    except (configparser.NoSectionError, configparser.NoOptionError, ValueError):
        return fallback

# 辅助函数：优先从环境变量获取，如果不存在则从配置文件获取
def get_env_or_config(env_key, section, config_key, fallback=None):
    """
    优先从环境变量获取配置，如果环境变量不存在则从配置文件获取
    
    环境变量优先级规则：
    - 如果环境变量存在（即使值为空字符串），使用环境变量的值
    - 如果环境变量不存在，从配置文件读取
    - 如果配置文件也不存在，使用 fallback 默认值
    
    Args:
        env_key: 环境变量名
        section: 配置文件节名
        config_key: 配置文件键名
        fallback: 如果都不存在时的默认值
    
    Returns:
        配置值（可能是字符串、None 或 fallback）
    """
    if env_key in os.environ:
        # 环境变量存在，优先使用（即使值为空字符串，也使用环境变量的值）
        value = os.environ[env_key]
        logger.debug(f"使用环境变量 {env_key}={value[:20] + '...' if value and len(value) > 20 else (value if value else '(空)')}")
        return value
    else:
        # 环境变量不存在，使用配置文件
        value = get_config(section, config_key, fallback)
        if value:
            logger.debug(f"使用配置文件 {section}.{config_key}={value[:20] + '...' if len(value) > 20 else value}")
        return value

# 从环境变量或配置文件获取配置（环境变量优先）
# Token 支持多个环境变量名：TOKEN（本项目约定）、BOT_TOKEN、TELEGRAM_BOT_TOKEN（社区惯用）。
# 注意：Fly.io 部署指南（fly.toml 注释 / deploy_flyio.sh / 文档）使用 BOT_TOKEN，
# 若只读 TOKEN 会导致按文档配置的部署直接启动失败，故此处必须做别名兼容。
TOKEN = (
    get_env_or_config('TOKEN', 'BOT', 'TOKEN')
    or os.getenv('BOT_TOKEN')
    or os.getenv('TELEGRAM_BOT_TOKEN')
)
CHANNEL_ID = (
    get_env_or_config('CHANNEL_ID', 'BOT', 'CHANNEL_ID')
    or os.getenv('CHANNEL')
)
DB_PATH = get_env_or_config('DB_PATH', 'BOT', 'DB_PATH', fallback='data/submissions.db')
TIMEOUT = int(get_env_or_config('TIMEOUT', 'BOT', 'TIMEOUT') or get_config_int('BOT', 'TIMEOUT', 300))
ALLOWED_TAGS = int(get_env_or_config('ALLOWED_TAGS', 'BOT', 'ALLOWED_TAGS') or get_config_int('BOT', 'ALLOWED_TAGS', 30))
NET_TIMEOUT = 120   # 网络请求超时时间（秒）

# OWNER_ID 需要转换为整数类型
_owner_id_str = get_env_or_config('OWNER_ID', 'BOT', 'OWNER_ID')
try:
    OWNER_ID = int(_owner_id_str) if _owner_id_str else None
except (ValueError, TypeError):
    OWNER_ID = None
    logger.warning(f"OWNER_ID 配置无效，无法转换为整数: {_owner_id_str}")

# ADMIN_IDS 管理员ID列表（用于管理命令）
_admin_ids_str = get_env_or_config('ADMIN_IDS', 'BOT', 'ADMIN_IDS') or ''
ADMIN_IDS = []
if _admin_ids_str:
    try:
        # 支持逗号分隔的多个ID
        ADMIN_IDS = [int(id.strip()) for id in _admin_ids_str.split(',') if id.strip()]
    except (ValueError, TypeError):
        logger.warning(f"ADMIN_IDS 配置无效: {_admin_ids_str}")
        ADMIN_IDS = []

# 如果设置了 OWNER_ID 且不在 ADMIN_IDS 中，自动添加
if OWNER_ID and OWNER_ID not in ADMIN_IDS:
    ADMIN_IDS.append(OWNER_ID)

# 布尔值配置：环境变量优先
_show_submitter_env = os.getenv('SHOW_SUBMITTER')
if _show_submitter_env is not None:
    SHOW_SUBMITTER = _show_submitter_env.lower() in ('true', '1', 'yes')
else:
    SHOW_SUBMITTER = get_config_bool('BOT', 'SHOW_SUBMITTER', True)

_notify_owner_env = os.getenv('NOTIFY_OWNER')
if _notify_owner_env is not None:
    NOTIFY_OWNER = _notify_owner_env.lower() in ('true', '1', 'yes')
else:
    NOTIFY_OWNER = get_config_bool('BOT', 'NOTIFY_OWNER', True)

BOT_MODE = get_env_or_config('BOT_MODE', 'BOT', 'BOT_MODE', fallback='MIXED')

# 允许的文件类型配置
ALLOWED_FILE_TYPES = get_env_or_config('ALLOWED_FILE_TYPES', 'BOT', 'ALLOWED_FILE_TYPES', fallback='*')

# 运行模式配置
_run_mode = get_env_or_config('RUN_MODE', 'BOT', 'RUN_MODE', fallback='POLLING')
RUN_MODE = _run_mode.strip().upper() if _run_mode else 'POLLING'

# Webhook 配置（仅当 RUN_MODE = WEBHOOK 时生效）
WEBHOOK_URL = get_env_or_config('WEBHOOK_URL', 'WEBHOOK', 'URL', fallback='')
_webhook_port = get_env_or_config('WEBHOOK_PORT', 'WEBHOOK', 'PORT')
WEBHOOK_PORT = int(_webhook_port) if _webhook_port else get_config_int('WEBHOOK', 'PORT', 8080)
WEBHOOK_PATH = get_env_or_config('WEBHOOK_PATH', 'WEBHOOK', 'PATH', fallback='/webhook')
WEBHOOK_SECRET_TOKEN = get_env_or_config('WEBHOOK_SECRET_TOKEN', 'WEBHOOK', 'SECRET_TOKEN', fallback='')

# 搜索引擎配置
SEARCH_INDEX_DIR = get_env_or_config('SEARCH_INDEX_DIR', 'SEARCH', 'INDEX_DIR', fallback='data/search_index')
_search_enabled_env = os.getenv('SEARCH_ENABLED')
if _search_enabled_env is not None:
    SEARCH_ENABLED = _search_enabled_env.lower() in ('true', '1', 'yes')
else:
    SEARCH_ENABLED = get_config_bool('SEARCH', 'ENABLED', True)
SEARCH_ANALYZER = (get_env_or_config('SEARCH_ANALYZER', 'SEARCH', 'ANALYZER', fallback='jieba') or 'jieba').strip().lower()
_search_highlight_env = os.getenv('SEARCH_HIGHLIGHT')
if _search_highlight_env is not None:
    SEARCH_HIGHLIGHT = _search_highlight_env.lower() in ('true', '1', 'yes')
else:
    SEARCH_HIGHLIGHT = get_config_bool('SEARCH', 'HIGHLIGHT', False)

# 数据库配置
_db_cache_kb = get_env_or_config('DB_CACHE_KB', 'DB', 'CACHE_SIZE_KB')
DB_CACHE_KB = int(_db_cache_kb) if _db_cache_kb else get_config_int('DB', 'CACHE_SIZE_KB', 4096)  # SQLite page cache，单位KB

# 投稿频率限制：每用户每小时最多发起投稿次数，0 为关闭
_submit_limit = get_env_or_config('SUBMIT_LIMIT_PER_HOUR', 'BOT', 'SUBMIT_LIMIT_PER_HOUR')
try:
    SUBMIT_LIMIT_PER_HOUR = int(_submit_limit) if _submit_limit else get_config_int('BOT', 'SUBMIT_LIMIT_PER_HOUR', 10)
except (ValueError, TypeError):
    SUBMIT_LIMIT_PER_HOUR = 10

# 验证必要配置
if not TOKEN:
    raise ValueError("❌ TOKEN 未设置！请在环境变量或 config.ini 中设置")
if not CHANNEL_ID:
    raise ValueError("❌ CHANNEL_ID 未设置！请在环境变量或 config.ini 中设置")

# 模式常量定义
MODE_MEDIA = 'MEDIA'      # 仅媒体上传
MODE_DOCUMENT = 'DOCUMENT'  # 仅文档上传
MODE_MIXED = 'MIXED'      # 混合模式

# 打印配置信息（调试用）
logger.info(f"配置加载完成:")
logger.info(f"  - BOT_MODE: {BOT_MODE}")
logger.info(f"  - RUN_MODE: {RUN_MODE}")
logger.info(f"  - CHANNEL_ID: {CHANNEL_ID}")
logger.info(f"  - DB_PATH: {DB_PATH}")
logger.info(f"  - TIMEOUT: {TIMEOUT}")
logger.info(f"  - OWNER_ID: {OWNER_ID if OWNER_ID else '未设置'}")
logger.info(f"  - ADMIN_IDS: {ADMIN_IDS if ADMIN_IDS else '未设置'}")
logger.info(f"  - ALLOWED_FILE_TYPES: {ALLOWED_FILE_TYPES}")
if RUN_MODE == 'WEBHOOK':
    logger.info(f"  - WEBHOOK_URL: {WEBHOOK_URL if WEBHOOK_URL else '未设置'}")
    logger.info(f"  - WEBHOOK_PORT: {WEBHOOK_PORT}")
    logger.info(f"  - WEBHOOK_PATH: {WEBHOOK_PATH}")
    logger.info(f"  - WEBHOOK_SECRET: {'已设置' if WEBHOOK_SECRET_TOKEN else '未设置（将自动生成）'}")
logger.info(f"  - SEARCH_INDEX_DIR: {SEARCH_INDEX_DIR}")
logger.info(f"  - SEARCH_ENABLED: {SEARCH_ENABLED}")
logger.info(f"  - SEARCH_ANALYZER: {SEARCH_ANALYZER}")
logger.info(f"  - SEARCH_HIGHLIGHT: {SEARCH_HIGHLIGHT}")
logger.info(f"  - DB_CACHE_KB: {DB_CACHE_KB}")