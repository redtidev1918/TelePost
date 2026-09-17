"""
配置文件读取和变量定义模块
"""
import os
import sys
import configparser
import logging

from utils.run_mode import resolve_run_mode

logger = logging.getLogger(__name__)

# 项目根目录：源码运行取仓库根；PyInstaller 冻结时取可执行文件所在目录，
# 保证 config.ini / data / logs 与 exe 同处一隅（双击即用）。
if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
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

# ---- 投稿者发布通知策略（§notify-submitter）---------------------------------
# Publication success 是唯一触发点（review approval 不是）：所有投稿路径
# （Chat DIRECT_PUBLISH / REVIEW_REQUIRED / EDITORIAL）共用同一条 durable
# 通知 pipeline。
#   off          不通知
#   published    只通知“已发布”
#   with_changes 另外附带 editorial change summary（无编辑时不加）
SUBMITTER_PUBLISH_NOTIFY = os.getenv(
    "SUBMITTER_PUBLISH_NOTIFY", get_config('BOT', 'SUBMITTER_PUBLISH_NOTIFY', 'off')
).strip().lower() or 'off'
if SUBMITTER_PUBLISH_NOTIFY not in ("off", "published", "with_changes"):
    SUBMITTER_PUBLISH_NOTIFY = 'off'


BOT_MODE = get_env_or_config('BOT_MODE', 'BOT', 'BOT_MODE', fallback='MIXED')

# 允许的文件类型配置
ALLOWED_FILE_TYPES = get_env_or_config('ALLOWED_FILE_TYPES', 'BOT', 'ALLOWED_FILE_TYPES', fallback='*')

# Webhook 与运行模式配置。AUTO 在存在有效公网 HTTPS URL 时选择 Webhook，
# 否则使用 Polling；run.py 多 bot 启动器会通过 RUN_MODE_REQUESTED 保留原始选择。
WEBHOOK_URL = get_env_or_config('WEBHOOK_URL', 'WEBHOOK', 'URL', fallback='')
_webhook_port = get_env_or_config('WEBHOOK_PORT', 'WEBHOOK', 'PORT')
WEBHOOK_PORT = int(_webhook_port) if _webhook_port else get_config_int('WEBHOOK', 'PORT', 8080)
WEBHOOK_PATH = get_env_or_config('WEBHOOK_PATH', 'WEBHOOK', 'PATH', fallback='/webhook')
WEBHOOK_SECRET_TOKEN = get_env_or_config('WEBHOOK_SECRET_TOKEN', 'WEBHOOK', 'SECRET_TOKEN', fallback='')

_run_mode = os.getenv('RUN_MODE_REQUESTED') or get_env_or_config(
    'RUN_MODE', 'BOT', 'RUN_MODE', fallback='AUTO'
)
RUN_MODE_REQUESTED = (_run_mode or 'AUTO').strip().upper()
RUN_MODE = resolve_run_mode(RUN_MODE_REQUESTED, WEBHOOK_URL)

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

# 单条投稿可包含的文件数；发布时会自动拆成 Telegram 每组 10 个相册。
try:
    MAX_SUBMISSION_FILES = max(1, int(get_env_or_config('API_MAX_FILES', 'BOT', 'API_MAX_FILES', fallback='100')))
except (ValueError, TypeError):
    MAX_SUBMISSION_FILES = 100

# 投稿频率限制：每用户每小时最多发起投稿次数，0 为关闭
_submit_limit = get_env_or_config('SUBMIT_LIMIT_PER_HOUR', 'BOT', 'SUBMIT_LIMIT_PER_HOUR')
try:
    SUBMIT_LIMIT_PER_HOUR = int(_submit_limit) if _submit_limit else get_config_int('BOT', 'SUBMIT_LIMIT_PER_HOUR', 10)
except (ValueError, TypeError):
    SUBMIT_LIMIT_PER_HOUR = 10

# 投稿审核来源开关。两个开关相互独立，可只审核 API、只审核聊天，或同时审核。
_api_review_required = get_env_or_config(
    'API_REVIEW_REQUIRED', 'BOT', 'API_REVIEW_REQUIRED', fallback='true'
)
API_REVIEW_REQUIRED = str(_api_review_required).lower() in ('true', '1', 'yes')

_miniapp_review_required = get_env_or_config(
    'MINIAPP_REVIEW_REQUIRED', 'BOT', 'MINIAPP_REVIEW_REQUIRED', fallback='true'
)
MINIAPP_REVIEW_REQUIRED = str(_miniapp_review_required).lower() in ('true', '1', 'yes')

_chat_review_required = get_env_or_config(
    'CHAT_REVIEW_REQUIRED', 'BOT', 'CHAT_REVIEW_REQUIRED', fallback='false'
)
CHAT_REVIEW_REQUIRED = str(_chat_review_required).lower() in ('true', '1', 'yes')

_review_chat_id = get_env_or_config('REVIEW_CHAT_ID', 'BOT', 'REVIEW_CHAT_ID', fallback='')
if _review_chat_id:
    try:
        REVIEW_CHAT_ID = int(_review_chat_id)
    except (ValueError, TypeError):
        REVIEW_CHAT_ID = str(_review_chat_id).strip()
else:
    REVIEW_CHAT_ID = None

# 验证必要配置
if not TOKEN:
    raise ValueError("❌ TOKEN 未设置！请在环境变量或 config.ini 中设置")
if not CHANNEL_ID:
    raise ValueError("❌ CHANNEL_ID 未设置！请在环境变量或 config.ini 中设置")
if (API_REVIEW_REQUIRED or MINIAPP_REVIEW_REQUIRED or CHAT_REVIEW_REQUIRED) and not REVIEW_CHAT_ID:
    raise ValueError(
        "❌ 已开启 MINIAPP_REVIEW_REQUIRED / API_REVIEW_REQUIRED / "
        "CHAT_REVIEW_REQUIRED，但 REVIEW_CHAT_ID 未设置"
    )

# 审核群绝不能与投稿频道是同一个会话：否则审核预览相册、控制消息和
# PixivFlow 空结果/失败通知都会以"回复/散帖"形式出现在频道里，
# 造成用户看到的"主贴后面莫名跟着几条奇怪回复"。
if REVIEW_CHAT_ID is not None:
    _chan = str(CHANNEL_ID).strip()
    _review = str(REVIEW_CHAT_ID).strip()
    if _chan == _review:
        raise ValueError(
            "❌ REVIEW_CHAT_ID 不能等于 CHANNEL_ID：审核群与投稿频道必须是"
            "两个不同的会话，否则预览/通知会混进频道"
        )
    if _chan.lstrip('-').isdigit() and _review.lstrip('-').isdigit():
        try:
            if int(_chan) == int(_review):
                raise ValueError(
                    "❌ REVIEW_CHAT_ID 不能等于 CHANNEL_ID：审核群与投稿频道"
                    "必须是两个不同的会话，否则预览/通知会混进频道"
                )
        except ValueError:
            pass

# 频道发布 footer（§submission-entrypoint）：正式发布到频道的帖子会在 caption
# 最下方追加文本导航 footer：
#   ✉️ TG 投稿  → https://t.me/<bot>?start=submit
#   📱 Mini App → https://t.me/<bot>?startapp=submit（MINIAPP_SUBMIT_CTA=true 时）
# 标签是固定展示契约，不读取 CHANNEL_FOOTER_TEXT（保留仅为兼容旧部署）。
CHANNEL_FOOTER_LINK = (
    get_env_or_config('CHANNEL_FOOTER_LINK', 'BOT', 'CHANNEL_FOOTER_LINK',
                      fallback='')
    or ''
).strip()
CHANNEL_FOOTER_TEXT = (
    get_env_or_config('CHANNEL_FOOTER_TEXT', 'BOT', 'CHANNEL_FOOTER_TEXT',
                      fallback='点击投稿')
    or '点击投稿'
).strip()

# 频道 footer 额外追加 Mini App 导航项。startapp 只是导航意图，身份仍由
# 服务器校验的 Telegram initData 决定。未启用或链接缺失时省略该导航项，
# 绝不为缺失配置生成坏链接。
MINIAPP_SUBMIT_CTA = str(
    get_env_or_config('MINIAPP_SUBMIT_CTA', 'BOT', 'MINIAPP_SUBMIT_CTA',
                      fallback='false')
    or 'false'
).strip().lower() in {'1', 'true', 'yes', 'on'}
# 可选发布增强：TXT 小说通过 TelePress 发布到 Telegraph 提供「在线阅读」
# （§telepress-preview）。它是可选 enrichment：默认关闭；开启后仍由 Telegram
# TXT document 决定 Publication 成功与否，Telegraph 失败/超时绝不回滚或失败发布。
# TELEGRAPH_ACCESS_TOKEN 是 Telegraph 账户 access token（可创建匿名账户获得），
# 仅作为 Secret 注入，绝不写日志。
_novel_preview_enabled = get_env_or_config(
    'NOVEL_PREVIEW_ENABLED', 'NOVEL_PREVIEW', 'ENABLED', fallback='false'
)
NOVEL_PREVIEW_ENABLED = str(_novel_preview_enabled).lower() in ('true', '1', 'yes')

_novel_preview_timeout = get_env_or_config(
    'NOVEL_PREVIEW_TIMEOUT_SECONDS', 'NOVEL_PREVIEW', 'TIMEOUT_SECONDS',
    fallback='15'
)
try:
    NOVEL_PREVIEW_TIMEOUT_SECONDS = max(1.0, float(_novel_preview_timeout))
except (ValueError, TypeError):
    NOVEL_PREVIEW_TIMEOUT_SECONDS = 15.0

_novel_preview_max_bytes = get_env_or_config(
    'NOVEL_PREVIEW_MAX_BYTES', 'NOVEL_PREVIEW', 'MAX_BYTES', fallback='4194304'
)
try:
    NOVEL_PREVIEW_MAX_BYTES = max(1024, int(_novel_preview_max_bytes))
except (ValueError, TypeError):
    NOVEL_PREVIEW_MAX_BYTES = 4 * 1024 * 1024

TELEGRAPH_ACCESS_TOKEN = (
    os.getenv('TELEGRAPH_ACCESS_TOKEN', '')
    or get_config('NOVEL_PREVIEW', 'TELEGRAPH_ACCESS_TOKEN', '')
    or ''
).strip()

# 模式常量定义
MODE_MEDIA = 'MEDIA'      # 仅媒体上传
MODE_DOCUMENT = 'DOCUMENT'  # 仅文档上传
MODE_MIXED = 'MIXED'      # 混合模式

# 打印配置信息（调试用）
logger.info(f"配置加载完成:")
logger.info(f"  - BOT_MODE: {BOT_MODE}")
logger.info(
    f"  - RUN_MODE: {RUN_MODE_REQUESTED} -> {RUN_MODE}"
    if RUN_MODE_REQUESTED == 'AUTO'
    else f"  - RUN_MODE: {RUN_MODE}"
)
logger.info(f"  - CHANNEL_ID: {CHANNEL_ID}")
logger.info(f"  - DB_PATH: {DB_PATH}")
logger.info(f"  - TIMEOUT: {TIMEOUT}")
logger.info(f"  - OWNER_ID: {OWNER_ID if OWNER_ID else '未设置'}")
logger.info(f"  - ADMIN_IDS: {ADMIN_IDS if ADMIN_IDS else '未设置'}")
logger.info(f"  - API_REVIEW_REQUIRED: {API_REVIEW_REQUIRED}")
logger.info(f"  - MINIAPP_REVIEW_REQUIRED: {MINIAPP_REVIEW_REQUIRED}")
logger.info(f"  - CHAT_REVIEW_REQUIRED: {CHAT_REVIEW_REQUIRED}")
logger.info(f"  - REVIEW_CHAT_ID: {REVIEW_CHAT_ID if REVIEW_CHAT_ID else '未设置'}")
logger.info(f"  - CHANNEL_FOOTER_LINK: {CHANNEL_FOOTER_LINK if CHANNEL_FOOTER_LINK else '未设置（不追加 footer）'}")
logger.info(f"  - ALLOWED_FILE_TYPES: {ALLOWED_FILE_TYPES}")
if RUN_MODE != "WEBHOOK":
    try:
        from handlers.publish import CHANNEL_ALBUM_REPLY as _album_reply
    except Exception:
        _album_reply = os.getenv("CHANNEL_ALBUM_REPLY", "chain").strip().lower()
    if _album_reply == "discussion":
        logger.warning(
            "  - ⚠️  CHANNEL_ALBUM_REPLY=discussion 仅在 Webhook 模式可用："
            "自动转发事件进不了 Polling 进程，多图发布会在等待转发超时后失败。"
            "请设置 RUN_MODE=WEBHOOK，或改回 chain/post。"
        )
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
