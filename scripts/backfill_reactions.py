#!/usr/bin/env python3
"""Backfill historical channel reaction counts (KNOWN_DEBT closure).

Telegram's Bot API cannot enumerate channel history, so posts reacted to
before the reaction handler shipped stay at heat 0. This script uses a USER
MTProto client (Pyrogram) to read each channel message's total reaction count
and feeds it through the same durable projection as live ingestion:

``message_reaction_counts`` → ``published_posts.reactions/heat_score``.

Credentials come from the environment and are never printed:

* ``PYROGRAM_API_ID`` / ``PYROGRAM_API_HASH`` — my.telegram.org app credentials
* ``PYROGRAM_SESSION_STRING`` or ``--session-file`` — an authorized client

Usage:

```bash
python3 scripts/backfill_reactions.py \
  --db data/bot1/submissions.db --channel @xgdShare --limit 200 --dry-run
python3 scripts/backfill_reactions.py \
  --db data/bot1/submissions.db --channel @xgdShare --limit 200 --apply
```

``--dry-run`` is the default and reports how many messages map to owned posts
without writing. Apply mode writes an audit event
``reaction.backfilled`` and refreshes search indexes.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"缺少环境变量 {name}（Pyrogram 用户客户端凭据）")
    return value


async def scrape_and_project(
    *,
    db_path: str,
    channel: str,
    limit: int,
    apply: bool,
    session_string: str,
    session_file: str,
) -> int:
    from pyrogram import Client

    from database import db_manager
    from telepost.application.reaction_backfill import normalize_counts, project_counts

    db_manager.DB_PATH = db_path
    from database.db_manager import init_db
    await init_db()

    if not session_string and not session_file:
        raise SystemExit(
            "需要 PYROGRAM_SESSION_STRING 或 --session-file（已授权的用户会话），"
            "二者至少提供其一。"
        )
    common = dict(
        api_id=int(_env("PYROGRAM_API_ID")),
        api_hash=_env("PYROGRAM_API_HASH"),
        in_memory=bool(session_string),
    )
    if session_string:
        client = Client("pixivflow-reaction-backfill", session_string=session_string, **common)
    else:
        client = Client(str(Path(session_file).resolve()), **common)

    raw_counts: list[tuple[int, int]] = []
    async with client:
        # ``limit=None`` is not accepted by ``get_chat_history``; negative means
        # unbounded in MTProto, but we keep an explicit cap for operator safety.
        count = 0
        async for message in client.get_chat_history(channel, limit=limit):
            count += 1
            reactions = getattr(message, "reactions", None)
            total = 0
            if reactions is not None:
                total = sum(
                    int(getattr(item, "count", 0) or 0)
                    for item in getattr(reactions, "reactions", [])
                )
            raw_counts.append((int(message.id), total))
        print(f"已读取 {count} 条频道消息（channel={channel}）")

    counts = normalize_counts(raw_counts)
    result = await project_counts(
        counts,
        dry_run=not apply,
        actor="operator:reaction-backfill",
    )
    print(
        f"模式={'apply' if apply else 'dry-run'} "
        f"messages={result['messages']} posts={result['posts']}"
    )
    if not apply and result["posts"]:
        print("预览无误后请加 --apply 真正写库。")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, help="SQLite 数据库路径（如 data/bot1/submissions.db）")
    parser.add_argument("--channel", required=True, help="频道 @username 或数字 id")
    parser.add_argument("--limit", type=int, default=200, help="最多读取多少条消息")
    parser.add_argument("--apply", action="store_true", help="写库（默认 dry-run）")
    parser.add_argument("--session-file", default="", help="Pyrogram session 文件路径")
    args = parser.parse_args()

    try:
        import pyrogram  # noqa: F401
    except ImportError:
        print(
            "Pyrogram 未安装。请先在独立环境执行: pip install pyrogram\n"
            "本脚本需要 my.telegram.org 的 API_ID/API_HASH 和用户会话，"
            "不会打印或提交凭据。",
            file=sys.stderr,
        )
        return 3

    session_string = os.environ.get("PYROGRAM_SESSION_STRING", "")
    if args.session_file and not Path(args.session_file).exists():
        print(f"session 文件不存在: {args.session_file}", file=sys.stderr)
        return 3
    return asyncio.run(
        scrape_and_project(
            db_path=args.db,
            channel=args.channel,
            limit=max(1, min(args.limit, 5000)),
            apply=args.apply,
            session_string=session_string,
            session_file=args.session_file,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
