"""Operator CLI for repairing a public channel root, defaulting to dry-run.

Uses a fresh Telegram public-channel observation because Bot API has no getMessage.
Private/deleted/changed/album messages fail closed; never forwards or sends a post.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime
from html.parser import HTMLParser
import json
import os
import re


class PublicRootParser(HTMLParser):
    def __init__(self, post):
        super().__init__(convert_charrefs=True)
        self.post = post
        self.active = False
        self.depth = 0
        self.text_depth = None
        self.text = []
        self.buttons = []
        self.button = None
        self.date = None
        self.photos = 0
        self.grouped = False
        self.found = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = attrs.get("class", "").split()
        if attrs.get("data-post") == self.post:
            self.active = True
            self.found += 1
        if not self.active:
            return
        if tag == "div":
            self.depth += 1
            if "tgme_widget_message_text" in classes:
                self.text_depth = self.depth
            if "tgme_widget_message_grouped_wrap" in classes:
                self.grouped = True
        if "tgme_widget_message_photo_wrap" in classes:
            self.photos += 1
        if tag == "time":
            self.date = attrs.get("datetime")
        if "tgme_widget_message_inline_button" in classes:
            href = attrs.get("href", "")
            if not href.startswith("https://"):
                raise ValueError("unsupported_current_keyboard")
            self.button = [href, []]
        if self.text_depth and tag == "br":
            self.text.append("\n")

    def handle_data(self, data):
        if self.active and self.text_depth:
            self.text.append(data)
        if self.active and self.button:
            self.button[1].append(data)

    def handle_endtag(self, tag):
        if not self.active:
            return
        if tag == "a" and self.button:
            self.buttons.append((self.button[0], "".join(self.button[1]).strip()))
            self.button = None
        if tag == "div":
            if self.depth == self.text_depth:
                self.text_depth = None
            self.depth -= 1
            if self.depth == 0:
                self.active = False


def parse_public_root(html, username, channel_id, message_id, bot):
    from telegram import Message, InlineKeyboardMarkup, InlineKeyboardButton
    parser = PublicRootParser(f"{username}/{message_id}")
    parser.feed(html)
    if parser.found != 1 or not parser.date or parser.photos > 1 or parser.grouped:
        raise ValueError("current_single_root_unavailable")
    from telepost.domain.navigation import READ_ONLINE_LABEL
    from ui.i18n import tr
    if any(text not in {READ_ONLINE_LABEL, tr(READ_ONLINE_LABEL)}
           for _url, text in parser.buttons):
        raise ValueError("unsupported_current_keyboard")
    markup = (InlineKeyboardMarkup([[InlineKeyboardButton(text, url=url)]
                                    for url, text in parser.buttons])
              if parser.buttons else None)
    return Message.de_json({
        "message_id": message_id,
        "date": int(datetime.fromisoformat(parser.date).timestamp()),
        "chat": {"id": channel_id, "type": "channel", "username": username},
        "text": "".join(parser.text),
        "reply_markup": markup.to_dict() if markup else None,
    }, bot)


async def read_public_root(bot, channel_id, message_id):
    import httpx
    channel = await bot.get_chat(channel_id)
    username = channel.username or ""
    if not re.fullmatch(r"[A-Za-z0-9_]+", username):
        raise ValueError("public_channel_required")
    async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
        response = await client.get(f"https://t.me/s/{username}/{message_id}",
                                    headers={"Cache-Control": "no-cache"})
        response.raise_for_status()
    return parse_public_root(response.text, username, channel.id, message_id, bot)


async def _run(args):
    from telegram import Bot
    from config.settings import CHANNEL_ID, TOKEN
    from telepost.application.preview_recovery import recover_preview
    from handlers.publish import _build_novel_preview, _make_txt_fetch
    async with Bot(TOKEN) as bot:
        root = await read_public_root(bot, CHANNEL_ID, args.message)
        if args.confirm_no_page or args.adopt_url:
            # Read the entire owning account, including paginated novels. Do
            # not turn a failed lookup, partial list or ambiguous match into absence.
            await _verify_pages(args.review, adopt_url=args.adopt_url)
        result = await recover_preview(
            review_id=args.review, bot=bot, channel_id=CHANNEL_ID,
            observed_root=root, actor=args.actor,
            dry_run=not args.apply, retry=args.retry,
            confirmed_absent=args.confirm_no_page, reconciled_url=args.adopt_url,
            enricher=_build_novel_preview(bot), fetch=_make_txt_fetch(bot),
            refresh_root=lambda: read_public_root(bot, CHANNEL_ID, args.message),
        )
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result.get("status") == "restored" or not args.apply else 1


async def _verify_pages(review_id, *, adopt_url=""):
    from config.settings import TELEGRAPH_ACCESS_TOKEN
    from telegraph import Telegraph
    from telepost.storage.sqlite.reviews import ReviewRepository
    from telepost.application.preview_recovery import _published_txt
    row = await ReviewRepository().get(review_id)
    if row is None:
        raise ValueError("review_missing")
    title, _items = await _published_txt(row)

    def check():
        client = Telegraph(access_token=TELEGRAPH_ACCESS_TOKEN)
        offset = 0
        found = False
        while True:
            result = client.get_page_list(offset=offset, limit=200)
            pages = result["pages"]
            for page in pages:
                matches = page["title"] == title or page["title"].startswith(title + " (")
                if adopt_url:
                    first = page["title"] == title or page["title"].startswith(title + " (1/")
                    if page["url"] == adopt_url and first:
                        found = True
                elif matches:
                    raise ValueError("existing_page_requires_reconciliation")
            offset += len(pages)
            if offset >= result["total_count"]:
                if adopt_url and not found:
                    raise ValueError("page_ownership_or_title_mismatch")
                return
            if not pages:
                raise ValueError("incomplete_page_list")
    await asyncio.wait_for(asyncio.to_thread(check), 30)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bot", type=int, required=True)
    parser.add_argument("--review", type=int, required=True)
    parser.add_argument("--message", type=int, required=True)
    parser.add_argument("--actor", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--retry", action="store_true")
    parser.add_argument("--confirm-no-page", action="store_true")
    parser.add_argument("--adopt-url", default="")
    args = parser.parse_args(argv)
    if min(args.bot, args.review, args.message) < 1:
        parser.error("ids must be positive")
    if args.adopt_url and (args.retry or args.confirm_no_page):
        parser.error("--adopt-url cannot generate pages")
    if args.confirm_no_page and not args.retry:
        parser.error("--confirm-no-page requires --retry")
    # Exactly the supervisor's per-Bot environment, BEFORE any settings imports.
    # Never import this CLI into a running Bot process to select another Bot.
    from run import build_bot_env
    env = build_bot_env(args.bot, dict(os.environ))
    if not env.get("TOKEN") or not env.get("CHANNEL_ID"):
        parser.error("selected Bot is not configured")
    os.environ.clear()
    os.environ.update(env)
    try:
        return asyncio.run(_run(args))
    except Exception as exc:
        # Exception messages may contain credentials; only safe codes are printed.
        from telepost.application.preview_recovery import PreviewRecoveryError
        code = str(exc) if isinstance(exc, (PreviewRecoveryError, ValueError)) and re.fullmatch(r"[a-z_]+", str(exc)) else type(exc).__name__
        print(json.dumps({"status": "blocked", "reason": code}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
