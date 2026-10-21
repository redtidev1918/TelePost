"""Resumable Telegram effects for terminal expired reviews.

Edits/deletes can safely repeat after a crash. A fallback send cannot: persist
an uncertain-send marker first, leaving an operator-visible record on crash.
"""
import json
import logging
from datetime import datetime, timezone
from urllib.parse import urlsplit

from telegram.error import BadRequest

from telepost.storage.sqlite.reviews import ReviewRepository
from ui.i18n import tr

logger = logging.getLogger(__name__)


def expiry_text(row, progress):
    def short(value, size):
        # Plain text, no parser; break @mentions in user-supplied metadata.
        return ' '.join(str(value or '').split()).replace('@', '@\u200b')[:size]

    def stamp(value):
        return datetime.fromtimestamp(value, timezone.utc).strftime('%Y-%m-%d %H:%M UTC')

    lines = [tr('投稿已自动过期'), '', tr('审核编号：#{id}').format(id=row['id']),
             tr('作品：{title}').format(title=short(row['title'], 160)),
             tr('来源：{source}').format(source=short(row['source_label'] or row['source'], 80))]
    link = str(row['link'] or '')
    try:
        parsed = urlsplit(link)
        if parsed.scheme in ('http', 'https') and parsed.hostname and not parsed.username and len(link) <= 500:
            lines.append(tr('作品链接：{link}').format(link=short(link, 500)))
    except ValueError:
        pass
    days = str(row['error']).split(' after ')[-1].split(' ')[0]
    lines.extend([
        tr('提交时间：{time}').format(time=stamp(row['created_at'])),
        tr('过期时间：{time}').format(time=stamp(row['decided_at'])),
        tr('原因：超过 {days} 天未审核').format(days=days), '',
        (tr('部分原审核媒体无法自动清理（Telegram 删除限制），需管理员手动清理。')
         if progress['blocked'] else tr('原审核媒体已清理。')),
        tr('此稿件已结束，不可再通过原按钮发布。'),
    ])
    return '\n'.join(lines)


async def finish_expiry(bot, row, *, now):
    repo = ReviewRepository()
    progress = json.loads(row['expiry_progress'])
    chat = row['review_chat_id']
    if str(chat).lstrip('-').isdigit():
        chat = int(chat)

    async def save(next_at=now):
        await repo.checkpoint_expiry(row['id'], progress, next_at=next_at)

    try:
        for message_id in list(progress['remaining']):
            if message_id == row['control_message_id']:
                progress['remaining'].remove(message_id)
                await save()
                continue
            try:
                await bot.delete_message(chat_id=chat, message_id=message_id)
            except BadRequest as exc:
                error = str(exc).lower()
                if 'message to delete not found' in error:
                    pass
                elif "message can't be deleted" in error or 'message cannot be deleted' in error:
                    progress['blocked'].append(message_id)
                else:
                    raise
            progress['remaining'].remove(message_id)
            await save()
        if not progress['card_done']:
            text = expiry_text(row, progress)
            missing = not row['control_message_id']
            if not missing:
                try:
                    await bot.edit_message_text(
                        chat_id=chat, message_id=row['control_message_id'],
                        text=text, parse_mode=None, reply_markup=None,
                        disable_web_page_preview=True)
                except BadRequest as exc:
                    error = str(exc).lower()
                    if 'message is not modified' in error:
                        pass
                    elif 'message to edit not found' in error or "message can't be edited" in error:
                        missing = True
                    else:
                        raise
            if missing:
                if progress['fallback_started']:
                    progress['last_error'] = 'fallback_send_uncertain: manual verification required'
                    await save(next_at=now + 86400)
                    logger.error('Expiry fallback needs verification: review_id=%s', row['id'])
                    return
                progress['fallback_started'] = True
                await save()
                result = await bot.send_message(chat_id=chat, text=text, parse_mode=None,
                                                disable_web_page_preview=True)
                progress['fallback_message_id'] = int(result.message_id)
            progress['card_done'] = True
        progress['last_error'] = 'media_delete_restricted' if progress['blocked'] else ''
        await save(next_at=None)
    except Exception as exc:
        # Persist only the exception class: API errors can contain credentials.
        progress['attempts'] += 1
        progress['last_error'] = type(exc).__name__
        await save(next_at=now + min(86400, 300 * 2 ** min(progress['attempts'], 8)))
        logger.warning('Expiry effects deferred: review_id=%s error_class=%s',
                       row['id'], type(exc).__name__)
