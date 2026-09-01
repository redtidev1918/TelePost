"""
处理器模块
"""
# 基础命令和会话控制
from handlers.mode_selection import start, select_mode
from handlers.command_handlers import cancel, debug, catch_all, help_command, settings

# 回调处理
from handlers.callback_handlers import handle_callback_query

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