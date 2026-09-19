"""
数据库管理模块
"""
import logging
import os
from datetime import datetime
from contextlib import asynccontextmanager
import aiosqlite

from config.settings import DB_PATH, TIMEOUT, DB_CACHE_KB

logger = logging.getLogger(__name__)

@asynccontextmanager
async def get_db():
    """
    数据库连接上下文管理器
    
    Yields:
        aiosqlite.Connection: 数据库连接对象
    """
    conn = await aiosqlite.connect(DB_PATH)
    conn.row_factory = aiosqlite.Row
    # 优化 SQLite 运行参数，降低 I/O 延迟
    try:
        await conn.execute("PRAGMA journal_mode=WAL;")
        await conn.execute("PRAGMA synchronous=NORMAL;")
        await conn.execute("PRAGMA temp_store=MEMORY;")
        # Writers serialize in WAL; wait up to 5s instead of throwing
        # "database is locked" to concurrent conditional UPDATEs (claims).
        await conn.execute("PRAGMA busy_timeout=5000;")
        # 通过负值设置 KB 为单位的 page cache 大小（默认为 4MB，可通过 DB_CACHE_KB 配置）
        await conn.execute(f"PRAGMA cache_size={-int(DB_CACHE_KB)};")
    except Exception:
        pass
    try:
        yield conn
        await conn.commit()
    except Exception as e:
        await conn.rollback()
        raise e
    finally:
        await conn.close()

async def init_db():
    """
    初始化数据库
    """
    try:
        async with get_db() as conn:
            # 临时投稿数据表
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS submissions (
                    user_id INTEGER PRIMARY KEY,
                    timestamp REAL,
                    mode TEXT,
                    image_id TEXT,
                    document_id TEXT,
                    tags TEXT,
                    link TEXT,
                    title TEXT,
                    note TEXT,
                    spoiler TEXT,
                    anonymous TEXT DEFAULT 'false',
                    username TEXT
                )
            ''')

            # 匿名投稿字段（存量表补列）
            try:
                await conn.execute("ALTER TABLE submissions ADD COLUMN anonymous TEXT DEFAULT 'false'")
                logger.info("已添加 anonymous 字段到 submissions 表")
            except Exception:
                pass  # 字段已存在

            # API 访问令牌（用于 /api/v1 自动化投稿，仅存哈希不存明文）
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS api_tokens (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    token_hash TEXT UNIQUE NOT NULL,
                    telegram_user_id INTEGER NOT NULL,
                    name TEXT DEFAULT '',
                    created_at REAL,
                    revoked INTEGER DEFAULT 0
                )
            ''')

            # API/聊天投稿审核队列。媒体先上传到私有审核群，此处只保存
            # Telegram file_id 与审核状态，避免 Fly 重启后丢失待审核文件。
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS pending_reviews (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    idempotency_key TEXT UNIQUE NOT NULL,
                    source TEXT NOT NULL DEFAULT 'api',
                    status TEXT NOT NULL DEFAULT 'pending',
                    user_id INTEGER NOT NULL,
                    username TEXT DEFAULT '',
                    title TEXT DEFAULT '',
                    tags TEXT DEFAULT '',
                    note TEXT DEFAULT '',
                    link TEXT DEFAULT '',
                    anonymous INTEGER DEFAULT 0,
                    spoiler INTEGER DEFAULT 0,
                    media_json TEXT NOT NULL DEFAULT '[]',
                    documents_json TEXT NOT NULL DEFAULT '[]',
                    review_chat_id TEXT NOT NULL,
                    review_message_ids TEXT NOT NULL DEFAULT '[]',
                    control_message_id INTEGER,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    decided_at REAL,
                    decided_by INTEGER,
                    published_message_id INTEGER,
                    target_id TEXT NOT NULL DEFAULT '',
                    source_label TEXT NOT NULL DEFAULT '',
                    source_ref TEXT NOT NULL DEFAULT '',
                    scheduled_at TEXT NOT NULL DEFAULT '',
                    error TEXT DEFAULT ''
                )
            ''')
            try:
                await conn.execute(
                    "ALTER TABLE pending_reviews "
                    "ADD COLUMN source TEXT NOT NULL DEFAULT 'api'"
                )
                logger.info("已添加 source 字段到 pending_reviews 表")
            except Exception:
                pass  # 字段已存在
            try:
                await conn.execute(
                    "ALTER TABLE pending_reviews "
                    "ADD COLUMN target_id TEXT NOT NULL DEFAULT ''"
                )
                logger.info("已添加 target_id 字段到 pending_reviews 表")
            except Exception:
                pass  # 字段已存在
            # Generic, bounded provenance for API submissions (any client).
            # TelePost only stores/displays these; it never interprets them.
            for column, ddl in (
                ("source_label", "TEXT NOT NULL DEFAULT ''"),
                ("source_ref", "TEXT NOT NULL DEFAULT ''"),
                ("scheduled_at", "TEXT NOT NULL DEFAULT ''"),
            ):
                try:
                    await conn.execute(
                        f"ALTER TABLE pending_reviews ADD COLUMN {column} {ddl}"
                    )
                except Exception:
                    pass  # 字段已存在
            await conn.execute(
                'CREATE INDEX IF NOT EXISTS idx_pending_reviews_status_created '
                'ON pending_reviews(status, created_at DESC)'
            )

            # Downstream reconciliation keys (PixivFlow ledger / historical dedupe).
            # Additive, nullable; older rows simply do not participate.
            for column, ddl in (
                ("pixiv_id", "TEXT NOT NULL DEFAULT ''"),
                ("work_type", "TEXT NOT NULL DEFAULT ''"),
                ("delivery_target", "TEXT NOT NULL DEFAULT ''"),
            ):
                try:
                    await conn.execute(
                        f"ALTER TABLE pending_reviews ADD COLUMN {column} {ddl}"
                    )
                except Exception:
                    pass  # column already exists
            await conn.execute(
                'CREATE INDEX IF NOT EXISTS idx_pending_reviews_dedupe '
                "ON pending_reviews(delivery_target, work_type, pixiv_id) "
                "WHERE pixiv_id <> ''"
            )

            # Review lineage for repeatable "重抓/换一张":
            #  - review_chain_id: stable identity of one logical review, shared
            #    by A → B → C replacement generations of the same review thread.
            #  - generation: 0 for the original, +1 per successful replacement.
            #  - supersedes_review_id: the review this one replaced (NULL/link
            #    to 0 for originals). Guards stale-button clicks and keeps the
            #    chain walkable without parsing Telegram captions.
            #  - refetch_request_id: request UUID of the refetch attempt that
            #    produced THIS review (empty for scheduled/original submissions).
            #    PixivFlow carries it in the submission payload; on arrival the
            #    old review is superseded only after this row is durably created
            #    (commit-after-success).
            for column, ddl in (
                ("review_chain_id", "TEXT NOT NULL DEFAULT ''"),
                ("generation", "INTEGER NOT NULL DEFAULT 0"),
                ("supersedes_review_id", "INTEGER"),
                ("refetch_request_id", "TEXT NOT NULL DEFAULT ''"),
            ):
                try:
                    await conn.execute(
                        f"ALTER TABLE pending_reviews ADD COLUMN {column} {ddl}"
                    )
                except Exception:
                    pass  # column already exists
            await conn.execute(
                'CREATE INDEX IF NOT EXISTS idx_pending_reviews_chain '
                'ON pending_reviews(review_chain_id, generation)'
            )
            await conn.execute(
                'CREATE INDEX IF NOT EXISTS idx_pending_reviews_supersedes '
                'ON pending_reviews(supersedes_review_id) WHERE supersedes_review_id IS NOT NULL'
            )

            # Identity / provenance model (§identity): the review's OWNER is
            # independent of the request ACTOR and the transport SOURCE.
            #   submitter_user_id / submitter_username: the verified HUMAN owner
            #     (NULL for service/automatic submissions), i.e. the only rows
            #     that may appear under /me/submissions.
            #   actor_kind / actor_subject: WHO/WHAT executed the request
            #     ('user' for Telegram/Mini App principals, 'service' for API
            #     tokens / automatic delivery), never used for ownership.
            # legacy user_id/username keep the request identity for display and
            # old idempotency normalization; service rows must NOT copy it into
            # submitter_user_id.
            for column, ddl in (
                ("submitter_user_id", "INTEGER"),
                ("submitter_username", "TEXT"),
                ("submitter_display_name", "TEXT"),
                ("actor_kind", "TEXT NOT NULL DEFAULT 'user'"),
                ("actor_subject", "TEXT NOT NULL DEFAULT ''"),
            ):
                try:
                    await conn.execute(
                        f"ALTER TABLE pending_reviews ADD COLUMN {column} {ddl}"
                    )
                except Exception:
                    pass  # column already exists
            await conn.execute(
                'CREATE INDEX IF NOT EXISTS idx_pending_reviews_submitter '
                'ON pending_reviews(submitter_user_id, created_at DESC, id DESC) '
                'WHERE submitter_user_id IS NOT NULL'
            )
            # User-facing history soft-delete (§my-submissions-delete): 1 hides
            # the whole review chain from the owner's /me history without
            # touching the review queue, published channel messages, or the
            # audit trail. Only terminal rows may be hidden (enforced in repo).
            try:
                await conn.execute(
                    "ALTER TABLE pending_reviews "
                    "ADD COLUMN hidden_from_submitter INTEGER NOT NULL DEFAULT 0"
                )
            except Exception:
                pass  # column already exists
            await conn.execute(
                'CREATE INDEX IF NOT EXISTS idx_pending_reviews_chain_hidden '
                'ON pending_reviews(review_chain_id, hidden_from_submitter)'
            )

            # Bootstrap lineage for EVERY existing review: a chain is anchored at
            # the first review we know about ("chain-<id>"), so old pending rows
            # stay refetchable with no re-submission. New replacements inherit
            # their source's chain id, so the original anchor never changes.
            await conn.execute(
                "UPDATE pending_reviews SET review_chain_id = 'chain-' || id "
                "WHERE review_chain_id = '' OR review_chain_id IS NULL"
            )

            # Durable refetch attempts: one row per user click / transport retry.
            #   state: requested → admitted → replaced | no_alternative | failed
            #          | obsolete.
            #   request_id  = durable idempotency key sent to PixivFlow; the same
            #                 id always maps to the same attempt (same slot).
            #   callback_key = Telegram callback_query.id (or a persisted UUID
            #                 fallback) so webhook redelivery of the SAME click
            #                 reuses the attempt instead of starting a second one.
            # The partial UNIQUE index is the DATA-LAYER one-active-per-chain
            # guard: only one non-terminal attempt may exist per review chain.
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS refetch_attempts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    callback_key TEXT NOT NULL UNIQUE,
                    request_id TEXT NOT NULL UNIQUE,
                    review_chain_id TEXT NOT NULL,
                    generation INTEGER NOT NULL DEFAULT 0,
                    source_review_id INTEGER NOT NULL,
                    source_candidate_id TEXT NOT NULL DEFAULT '',
                    result_candidate_id TEXT NOT NULL DEFAULT '',
                    state TEXT NOT NULL DEFAULT 'requested',
                    slot_id TEXT NOT NULL DEFAULT '',
                    failure_code TEXT NOT NULL DEFAULT '',
                    scanned INTEGER NOT NULL DEFAULT 0,
                    skipped_duplicate INTEGER NOT NULL DEFAULT 0,
                    skipped_invalid INTEGER NOT NULL DEFAULT 0,
                    skipped_unavailable INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL,
                    started_at REAL,
                    finished_at REAL,
                    last_progress_notified_at REAL
                )
            ''')
            await conn.execute(
                'CREATE UNIQUE INDEX IF NOT EXISTS idx_refetch_one_active '
                "ON refetch_attempts(review_chain_id) "
                "WHERE state IN ('requested','admitted')"
            )
            await conn.execute(
                'CREATE INDEX IF NOT EXISTS idx_refetch_attempts_chain '
                'ON refetch_attempts(review_chain_id, created_at DESC)'
            )
            # Older TelePost databases: add the progress-watchdog column
            # idempotently (the CREATE TABLE above carries it for fresh DBs).
            try:
                await conn.execute(
                    "ALTER TABLE refetch_attempts "
                    "ADD COLUMN last_progress_notified_at REAL"
                )
            except Exception:
                pass  # column already exists

            # Candidate history per review chain: every work already shown to
            # reviewers for this chain. (review_chain_id, candidate_id) is the
            # semantic unique key; candidate identity is the canonical Pixiv work
            # id (pending_reviews.pixiv_id), never parsed from captions.
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS refetch_seen_candidates (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    review_chain_id TEXT NOT NULL,
                    candidate_id TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT 'original',
                    generation INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL,
                    UNIQUE(review_chain_id, candidate_id)
                )
            ''')
            # Durable manual RECOVERY attempts (§manual-recovery): re-running a
            # FAILED schedule target under a server-defined policy preset.
            # callback_key UNIQUE makes button-click redelivery idempotent; the
            # partial UNIQUE index makes one-active-recovery-per-target a data
            # layer guarantee.
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS recovery_attempts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    callback_key TEXT NOT NULL UNIQUE,
                    request_id TEXT NOT NULL UNIQUE,
                    slot_id TEXT NOT NULL DEFAULT '',
                    schedule_id TEXT NOT NULL DEFAULT '',
                    target_id TEXT NOT NULL,
                    retry_mode TEXT NOT NULL DEFAULT 'normal',
                    state TEXT NOT NULL DEFAULT 'requested',
                    failure_code TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL,
                    finished_at REAL
                )
            ''')
            await conn.execute(
                'CREATE UNIQUE INDEX IF NOT EXISTS idx_recovery_one_active '
                "ON recovery_attempts(target_id) "
                "WHERE state IN ('requested','accepted')"
            )
            await conn.execute(
                'CREATE INDEX IF NOT EXISTS idx_recovery_target '
                'ON recovery_attempts(target_id, created_at DESC)'
            )
            # Durable dedup for terminal SCHEDULE outcome notifications
            # (§schedule-notify): one terminal summary per slot is ever sent to
            # the review/admin group, even when the same external clock replays
            # the slot or PixivFlow redelivers the outcome outbox intent.
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS schedule_outcome_notifications (
                    slot_id TEXT PRIMARY KEY,
                    sent_at REAL NOT NULL,
                    status TEXT NOT NULL DEFAULT ''
                )
            ''')

            # Bootstrap seen-history for existing reviews (current candidate only;
            # older chain history is not reconstructible and is documented as the
            # migration boundary). INSERT OR IGNORE keeps this idempotent.
            await conn.execute(
                "INSERT OR IGNORE INTO refetch_seen_candidates "
                "(review_chain_id, candidate_id, source, generation, created_at) "
                "SELECT review_chain_id, pixiv_id, 'original', generation, created_at "
                "FROM pending_reviews WHERE pixiv_id <> ''"
            )

            # ---- Editorial Revision (§editorial) -------------------------------
            # Reviewer edits never mutate the ORIGINAL pending_reviews row: each
            # revision stores its own immutable base/edited snapshots, a structured
            # change set and a human summary. The Review FSM stays the sole owner
            # of "may this publish?"; revisions only answer "which version".
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS editorial_revisions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    review_id INTEGER NOT NULL,
                    review_chain_id TEXT NOT NULL DEFAULT '',
                    revision_number INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'draft',
                    base_snapshot TEXT NOT NULL DEFAULT '{}',
                    edited_snapshot TEXT NOT NULL DEFAULT '{}',
                    change_set TEXT NOT NULL DEFAULT '{}',
                    summary TEXT NOT NULL DEFAULT '',
                    severity TEXT NOT NULL DEFAULT 'minor',
                    editor_user_id INTEGER,
                    editor_username TEXT NOT NULL DEFAULT '',
                    editor_display_name TEXT NOT NULL DEFAULT '',
                    version INTEGER NOT NULL DEFAULT 1,
                    published_message_id INTEGER,
                    published_snapshot TEXT NOT NULL DEFAULT '{}',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    finalized_at REAL,
                    published_at REAL,
                    UNIQUE(review_id, revision_number)
                )
            ''')
            await conn.execute(
                'CREATE INDEX IF NOT EXISTS idx_editorial_revisions_review '
                'ON editorial_revisions(review_id, revision_number)'
            )
            await conn.execute(
                'CREATE INDEX IF NOT EXISTS idx_editorial_revisions_chain '
                'ON editorial_revisions(review_chain_id, status)'
            )
            # Publication linkage: which revision (if any) produced the confirmed
            # channel post. NULL = original direct publish (backward-compatible).
            try:
                await conn.execute(
                    'ALTER TABLE pending_reviews ADD COLUMN '
                    'published_source_revision_id INTEGER'
                )
            except Exception:
                pass  # column already exists

            # ---- Submitter publication notification (durable + idempotent) -----
            # Enqueued at publish time, delivered by the periodic worker. One row
            # per (review, submitter): replay of the same publish can never send a
            # second Telegram message. Failures retry; the publication stands
            # regardless (§41-§42).
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS submitter_notifications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    -- NULL for DIRECT_PUBLISH (no review row exists): the
                    -- notification is keyed by the PUBLICATION, not the review.
                    review_id INTEGER,
                    revision_id INTEGER,
                    telegram_user_id INTEGER NOT NULL,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    kind TEXT NOT NULL DEFAULT 'published',
                    state TEXT NOT NULL DEFAULT 'pending',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT NOT NULL DEFAULT '',
                    message_id INTEGER,
                    payload TEXT NOT NULL DEFAULT '{}',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    sent_at REAL
                )
            ''')
            await conn.execute(
                'CREATE INDEX IF NOT EXISTS idx_submitter_notifications_state '
                'ON submitter_notifications(state, updated_at)'
            )

            # API 运维通知的持久幂等记录。不同 token 所绑定的用户可以复用同一业务键；
            # 同一用户在 Bot 重启后仍不会重复发送同一条通知。
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS api_notifications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_user_id INTEGER NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    message_id INTEGER,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    UNIQUE(telegram_user_id, idempotency_key)
                )
            ''')
            await conn.execute(
                'CREATE INDEX IF NOT EXISTS idx_api_notifications_updated '
                'ON api_notifications(updated_at)'
            )
            
            # 已发布帖子表（用于热度统计和搜索）
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS published_posts (
                    message_id INTEGER PRIMARY KEY,
                    user_id INTEGER,
                    username TEXT,
                    title TEXT,
                    tags TEXT,
                    link TEXT,
                    note TEXT,
                    content_type TEXT,
                    file_ids TEXT,
                    caption TEXT,
                    filename TEXT,
                    publish_time REAL,
                    views INTEGER DEFAULT 0,
                    forwards INTEGER DEFAULT 0,
                    reactions INTEGER DEFAULT 0,
                    heat_score REAL DEFAULT 0,
                    last_update REAL,
                    related_message_ids TEXT,
                    is_deleted INTEGER DEFAULT 0
                )
            ''')
            
            # 添加 is_deleted 字段（如果表已存在但没有该字段）
            try:
                await conn.execute('ALTER TABLE published_posts ADD COLUMN is_deleted INTEGER DEFAULT 0')
                logger.info("已添加 is_deleted 字段到 published_posts 表")
            except Exception:
                # 字段已存在，忽略错误
                pass
            
            # 创建索引以提升查询性能
            await conn.execute('CREATE INDEX IF NOT EXISTS idx_heat_score ON published_posts(heat_score DESC)')
            await conn.execute('CREATE INDEX IF NOT EXISTS idx_publish_time ON published_posts(publish_time DESC)')
            await conn.execute('CREATE INDEX IF NOT EXISTS idx_user_id ON published_posts(user_id)')
            await conn.execute('CREATE INDEX IF NOT EXISTS idx_tags ON published_posts(tags)')
            await conn.execute('CREATE INDEX IF NOT EXISTS idx_is_deleted ON published_posts(is_deleted)')

            # 组合索引：覆盖 /hot、/myposts 等高频查询的
            # "WHERE is_deleted = 0 [+ user_id] ORDER BY ..." 热路径，
            # 避免在 is_deleted 单列索引上回表后再排序
            await conn.execute('CREATE INDEX IF NOT EXISTS idx_deleted_heat ON published_posts(is_deleted, heat_score DESC)')
            await conn.execute('CREATE INDEX IF NOT EXISTS idx_deleted_publish_time ON published_posts(is_deleted, publish_time DESC)')
            await conn.execute('CREATE INDEX IF NOT EXISTS idx_user_deleted ON published_posts(user_id, is_deleted)')

            # Direct-publish delivery ledger (API_REVIEW_REQUIRED=false). The review
            # path already dedupes on pending_reviews.idempotency_key; direct publish had
            # NO idempotency, so an ACK loss / client retry re-posted to the channel.
            # One row per confirmed channel post, keyed by the caller's idempotency key.
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS delivery_ledger (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    idempotency_key TEXT UNIQUE NOT NULL,
                    target_id TEXT NOT NULL DEFAULT '',
                    pixiv_id TEXT NOT NULL DEFAULT '',
                    work_type TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'published',
                    message_id INTEGER,
                    related_message_ids TEXT NOT NULL DEFAULT '[]',
                    progress_json TEXT NOT NULL DEFAULT '[]',
                    user_id INTEGER,
                    created_at REAL NOT NULL
                )
            ''')
            try:
                await conn.execute(
                    "ALTER TABLE delivery_ledger ADD COLUMN "
                    "progress_json TEXT NOT NULL DEFAULT '[]'"
                )
            except Exception:
                pass
            await conn.execute(
                'CREATE INDEX IF NOT EXISTS idx_delivery_ledger_dedupe '
                "ON delivery_ledger(target_id, work_type, pixiv_id) "
                "WHERE pixiv_id <> ''"
            )

            # Optional publication enrichment: one durable row per PUBLICATION
            # (the delivery idempotency key), so a Telegram delivery retry
            # reuses an existing Telegraph preview instead of creating a second
            # page. A failed/unavailable preview never blocks the TXT publish.
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS publication_previews (
                    publication_key TEXT PRIMARY KEY,
                    provider TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT '',
                    url TEXT NOT NULL DEFAULT '',
                    title TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
            ''')

            # Append-only audit/observability log. pending_reviews remains the
            # source of truth; these rows are evidence only and are pruned by
            # cleanup_old_data (never while the referenced review is still open).
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS audit_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL NOT NULL,
                    component TEXT DEFAULT 'telepost',
                    event TEXT NOT NULL,
                    review_id INTEGER,
                    pixiv_id TEXT,
                    work_type TEXT,
                    target_id TEXT,
                    idempotency_key TEXT,
                    execution_id TEXT,
                    actor TEXT,
                    error_class TEXT,
                    detail TEXT
                )
            ''')
            await conn.execute(
                'CREATE INDEX IF NOT EXISTS idx_audit_events_review '
                'ON audit_events(review_id)'
            )
            await conn.execute(
                'CREATE INDEX IF NOT EXISTS idx_audit_events_execution '
                'ON audit_events(execution_id)'
            )
            await conn.execute(
                'CREATE INDEX IF NOT EXISTS idx_audit_events_ts '
                'ON audit_events(ts)'
            )

            # Moderation blocks (operator deny list). A block never deletes
            # history; it only stops NEW submissions from that subject.
            # subject is the canonical actor identity ('user:<id>' | 'api:<id>').
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS moderation_blocks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    subject TEXT NOT NULL UNIQUE,
                    reason TEXT NOT NULL DEFAULT '',
                    created_by INTEGER NOT NULL,
                    created_at REAL NOT NULL
                )
            """)
            await conn.execute(
                'CREATE INDEX IF NOT EXISTS idx_moderation_blocks_subject '
                'ON moderation_blocks(subject)'
            )

            # Durable Role Bindings (Admin Control Plane RBAC). Grants reviewer
            # / admin to a Telegram user on top of the OWNER_ID/ADMIN_IDS env
            # baseline; OWNER_ID stays break-glass root regardless of bindings.
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS role_bindings (
                    principal_kind TEXT NOT NULL,
                    principal_id INTEGER NOT NULL,
                    role TEXT NOT NULL,
                    created_by TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL,
                    PRIMARY KEY (principal_kind, principal_id, role)
                )
            """)

            # Delivery Asset Contract (Step 10): canonical media references on a
            # review chain so TelePost never has to swallow every PixivFlow domain
            # field. asset_id/source_url is the minimal contract; local artifacts
            # stay the file_id/media_json path unchanged.
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS media_asset_refs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    review_chain_id TEXT NOT NULL,
                    asset_id TEXT NOT NULL,
                    kind TEXT NOT NULL DEFAULT 'image',
                    source_url TEXT NOT NULL,
                    mime_type TEXT NOT NULL DEFAULT '',
                    file_id TEXT NOT NULL DEFAULT '',
                    file_unique_id TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL,
                    UNIQUE(review_chain_id, asset_id)
                )
            """)
            await conn.execute(
                'CREATE INDEX IF NOT EXISTS idx_media_asset_refs_chain '
                'ON media_asset_refs(review_chain_id)'
            )
            # Step 12 TelegramMediaCache: canonical Telegram delivery facts are
            # carried on the SAME asset row instead of a separate cache table.
            # Existing deployments get the columns via idempotent ADD COLUMN.
            for _column, _ddl in (
                ("file_id", "ALTER TABLE media_asset_refs ADD COLUMN file_id TEXT NOT NULL DEFAULT ''"),
                ("file_unique_id", "ALTER TABLE media_asset_refs ADD COLUMN file_unique_id TEXT NOT NULL DEFAULT ''"),
            ):
                try:
                    await conn.execute(_ddl)
                except Exception:
                    pass  # column already exists

            # published_posts 是频道现状，pending_reviews 是审核审计。频道消息被软删除时
            # 同步把对应审核记录从“曾发布”推进到“已删除”，避免把历史终态误当成
            # 当前仍在线的发布。触发器覆盖项目内所有软删除入口。
            await conn.execute('''
                CREATE TRIGGER IF NOT EXISTS trg_published_post_review_deleted
                AFTER UPDATE OF is_deleted ON published_posts
                WHEN NEW.is_deleted = 1 AND OLD.is_deleted IS NOT 1
                BEGIN
                    UPDATE pending_reviews
                    SET status = 'deleted',
                        updated_at = CAST(strftime('%s', 'now') AS REAL),
                        error = CASE
                            WHEN COALESCE(error, '') = '' THEN 'published message deleted'
                            ELSE error
                        END
                    WHERE published_message_id = NEW.message_id
                      AND status = 'published';
                END
            ''')

            # 一次性修正触发器上线前已经出现的错位记录。保留 decided_at/decided_by，
            # 因为它们仍表示当时的批准操作；只更新当前生命周期状态。
            cursor = await conn.execute('''
                UPDATE pending_reviews
                SET status = 'deleted',
                    updated_at = CAST(strftime('%s', 'now') AS REAL),
                    error = CASE
                        WHEN COALESCE(error, '') = '' THEN 'published message deleted'
                        ELSE error
                    END
                WHERE status = 'published'
                  AND published_message_id IN (
                      SELECT message_id FROM published_posts WHERE is_deleted = 1
                  )
            ''')
            if cursor.rowcount:
                logger.info("已同步 %d 条已删除发布的审核状态", cursor.rowcount)
            
            await conn.commit()
            logger.info("数据库初始化完成")
    except Exception as e:
        logger.error(f"初始化数据库时出错: {e}")
        raise

async def cleanup_old_data():
    """
    清理过期的会话数据与已决审核记录
    """
    try:
        # 首先检查表是否存在
        async with aiosqlite.connect(DB_PATH) as conn:
            c = await conn.cursor()
            await c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='submissions'")
            table_exists = await c.fetchone()
            
        if not table_exists:
            logger.warning("submissions 表不存在，跳过清理")
            return
            
        # 如果表存在，执行清理
        async with get_db() as conn:
            c = await conn.cursor()
            cutoff = datetime.now().timestamp() - TIMEOUT
            await c.execute("DELETE FROM submissions WHERE timestamp < ?", (cutoff,))

        # 清理终态（拒绝/发布/已删除/失败/过期）且超过保留期的审核记录，避免表无限增长。
        # 只清理终态记录：pending 的投稿绝不能动。保留期默认 30 天。
        review_retention_days = int(os.getenv("REVIEW_RETENTION_DAYS", "30"))
        if review_retention_days > 0:
            async with aiosqlite.connect(DB_PATH) as conn:
                c = await conn.cursor()
                await c.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='pending_reviews'"
                )
                has_reviews = await c.fetchone()
            if has_reviews:
                review_cutoff = datetime.now().timestamp() - review_retention_days * 86400
                async with get_db() as conn:
                    c = await conn.cursor()
                    await c.execute(
                        "DELETE FROM pending_reviews "
                        "WHERE status IN ('rejected','published','deleted','failed','expired') AND updated_at < ?",
                        (review_cutoff,),
                    )
                    logger.info("已清理过期审核记录（保留 %d 天）", review_retention_days)

                async with get_db() as conn:
                    await conn.execute(
                        "DELETE FROM api_notifications WHERE updated_at < ?",
                        (review_cutoff,),
                    )
                    logger.info("已清理过期 API 通知幂等记录（保留 %d 天）", review_retention_days)

        # Append-only audit: prune old rows, but keep every event attached to a
        # still-open review (preparing/pending/publishing/failed) regardless of
        # age. Unattached events (review_id IS NULL) follow the age cutoff.
        audit_retention_days = int(os.getenv("AUDIT_RETENTION_DAYS", "30"))
        if audit_retention_days > 0:
            audit_cutoff = datetime.now().timestamp() - audit_retention_days * 86400
            async with aiosqlite.connect(DB_PATH) as conn:
                c = await conn.cursor()
                await c.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='audit_events'"
                )
                has_audit = await c.fetchone()
            if has_audit:
                async with get_db() as conn:
                    cursor = await conn.execute(
                        "DELETE FROM audit_events WHERE ts < ? AND ("
                        "review_id IS NULL OR review_id NOT IN ("
                        "SELECT id FROM pending_reviews WHERE status IN "
                        "('preparing', 'pending', 'publishing', 'failed')"
                        "))",
                        (audit_cutoff,),
                    )
                    logger.debug("已清理过期审计事件 %d 条（保留 %d 天）",
                                 cursor.rowcount, audit_retention_days)
    except Exception as e:
        logger.error(f"清理过期数据失败: {e}")


async def claim_api_notification(telegram_user_id: int, idempotency_key: str) -> bool:
    """Atomically claim a notification key, surviving process restarts.

    A stale pending claim can be reclaimed after five minutes. This covers a
    process crash between the database claim and the Telegram request while
    suppressing ordinary concurrent duplicates.
    """
    now = datetime.now().timestamp()
    async with get_db() as conn:
        cursor = await conn.execute(
            """
            INSERT OR IGNORE INTO api_notifications (
                telegram_user_id, idempotency_key, status, created_at, updated_at
            ) VALUES (?, ?, 'pending', ?, ?)
            """,
            (telegram_user_id, idempotency_key, now, now),
        )
        if cursor.rowcount == 1:
            return True
        cursor = await conn.execute(
            """
            UPDATE api_notifications
            SET updated_at = ?
            WHERE telegram_user_id = ? AND idempotency_key = ?
              AND status = 'pending' AND updated_at < ?
            """,
            (now, telegram_user_id, idempotency_key, now - 300),
        )
        return cursor.rowcount == 1


async def mark_api_notification_sent(
    telegram_user_id: int,
    idempotency_key: str,
    message_id: int,
) -> None:
    async with get_db() as conn:
        await conn.execute(
            """
            UPDATE api_notifications
            SET status = 'sent', message_id = ?, updated_at = ?
            WHERE telegram_user_id = ? AND idempotency_key = ?
            """,
            (message_id, datetime.now().timestamp(), telegram_user_id, idempotency_key),
        )


async def release_api_notification(telegram_user_id: int, idempotency_key: str) -> None:
    """Release a failed pre-send claim so the caller's durable retry can proceed."""
    async with get_db() as conn:
        await conn.execute(
            """
            DELETE FROM api_notifications
            WHERE telegram_user_id = ? AND idempotency_key = ? AND status = 'pending'
            """,
            (telegram_user_id, idempotency_key),
        )
