"""
处理器模块
"""
# 基础命令和会话控制
from handlers.mode_selection import start, select_mode
from handlers.command_handlers import cancel, debug, catch_all, help_command, settings

# 回调处理
from handlers.callback_handlers import handle_callback_query

# 为了兼容性创建settings_callback别名
settings_callback = handle_callback_query

# 媒体和文档处理（临时占位符）
handle_image = lambda update, context: None
done_image = lambda update, context: None
handle_document = lambda update, context: None
done_document = lambda update, context: None

# 文本处理（临时占位符）
handle_text = lambda update, context: None
collect_extra = lambda update, context: None

# 媒体处理函数
from handlers.media_handlers import handle_media, done_media, prompt_media, skip_media, switch_to_doc_mode

# 文档处理函数
from handlers.document_handlers import handle_doc, done_doc, prompt_doc

# 提交处理函数
from handlers.submit_handlers import (
    handle_tag,
    handle_link,
    handle_title,
    handle_note,
    skip_optional_link,
    skip_optional_title,
    skip_optional_note
)

# 投稿处理
from handlers.publish import publish_submission