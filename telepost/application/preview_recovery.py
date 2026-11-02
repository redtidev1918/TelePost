"""Controlled preview repair. Never invokes publication or sends attachments.

An adapter must supply an observed root Message; this service verifies it against
both durable publication ledgers, the owning channel, and current Bot permission.
"""
from __future__ import annotations

import json
from urllib.parse import urlparse

from database.db_manager import get_db
from telepost.domain.delivery import MediaItem
from telepost.domain.editorial import Snapshot
from telepost.domain.navigation import READ_ONLINE_LABEL
from telepost.observability import audit
from telepost.storage.sqlite.ledger import DeliveryLedgerRepository
from telepost.storage.sqlite.novel_preview import PublicationPreviewRepository
from telepost.storage.sqlite.reviews import ReviewRepository


class PreviewRecoveryError(ValueError):
    pass


def valid_preview_url(url):
    parsed = urlparse(str(url or ""))
    return (parsed.scheme == "https" and parsed.hostname in {"telegra.ph", "graph.org"}
            and not parsed.username and not parsed.password and bool(parsed.path.strip("/")))


async def recover_preview(*, review_id, bot, channel_id, observed_root,
                          enricher=None, fetch=None, actor, dry_run=True,
                          retry=False, confirmed_absent=False, reconciled_url="",
                          refresh_root=None):
    """Repair only the confirmed original root. Caller authorizes the operator.

    ``observed_root`` must come from a trusted Telegram read, never JSON supplied
    by an untrusted HTTP client. Missing/current-message evidence fails closed.
    Timeout/provider ambiguity requires remote reconciliation before ``retry``.
    """
    row = await ReviewRepository().get(int(review_id))
    if row is None or row["status"] != "published" or not row["idempotency_key"]:
        raise PreviewRecoveryError("review_not_published")
    key = f"review:{review_id}:{row['idempotency_key']}"[:240]
    ledger = await DeliveryLedgerRepository().find_by_key(key)
    if (ledger is None or ledger.status != "published" or not ledger.message_id
            or ledger.message_id != row["published_message_id"]):
        raise PreviewRecoveryError("publication_identity_mismatch")
    channel = await bot.get_chat(channel_id)
    me = await bot.get_me()
    member = await bot.get_chat_member(channel.id, me.id)
    if (channel.type != "channel" or member.status not in {"creator", "administrator"}
            or (member.status != "creator" and not member.can_edit_messages)):
        raise PreviewRecoveryError("channel_edit_permission_required")
    # The stored post establishes root content/attachment and links this channel
    # observation to the original publication. Neither review nor ledger mutates.
    async with get_db() as conn:
        post = await (await conn.execute(
            "SELECT * FROM published_posts WHERE message_id=?", (ledger.message_id,),
        )).fetchone()
    if post is None:
        raise PreviewRecoveryError("published_post_missing")
    def validate_root(root):
        if (root is None or root.chat.id != channel.id
                or root.message_id != ledger.message_id or root.media_group_id):
            raise PreviewRecoveryError("current_single_root_required")
        current_text = str(root.caption or root.text or "")
        if not post["title"] or str(post["title"]) not in current_text:
            raise PreviewRecoveryError("current_root_content_mismatch")
        if abs(root.date.timestamp() - ledger.created_at) > 30:
            raise PreviewRecoveryError("current_root_date_mismatch")
    validate_root(observed_root)
    record = await PublicationPreviewRepository().find(key)
    result = {"review_id": review_id, "message_id": ledger.message_id,
              "status": record.status if record else "missing",
              "url": record.url if record else "", "dry_run": dry_run}
    if dry_run:
        return result
    await audit.record_event("preview.recovery_requested", review_id=review_id,
                             actor=str(actor), idempotency_key=key,
                             detail={"message_id": ledger.message_id, "retry": retry})
    url = record.url if record and record.status == "succeeded" else ""
    if not url and reconciled_url:
        if not valid_preview_url(reconciled_url):
            raise PreviewRecoveryError("invalid_preview_url")
        if not await PublicationPreviewRepository().adopt(key, reconciled_url):
            raise PreviewRecoveryError("preview_adoption_conflict")
        url = reconciled_url
    if not url:
        if enricher is None or not retry:
            raise PreviewRecoveryError("preview_generation_retry_required")
        title, items = await _published_txt(row)
        preview = await enricher.enrich(publication_key=key, title=title, items=items,
                                        fetch=fetch, retry=True,
                                        confirmed_absent=confirmed_absent)
        if not preview.succeeded:
            return {**result, "status": preview.status.value, "reason": preview.reason}
        url = preview.url
    if not valid_preview_url(url):
        raise PreviewRecoveryError("invalid_preview_url")
    if refresh_root is not None:
        observed_root = await refresh_root()
        validate_root(observed_root)
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    from ui.i18n import tr
    # Preserve unrelated buttons and collapse duplicate read-online buttons.
    old = observed_root.reply_markup
    rows = []
    for buttons in (old.inline_keyboard if old else []):
        kept = [button for button in buttons
                if button.text not in {READ_ONLINE_LABEL, tr(READ_ONLINE_LABEL)}
                and button.url != url]
        if kept:
            rows.append(kept)
    rows.insert(0, [InlineKeyboardButton(tr(READ_ONLINE_LABEL), url=url)])
    markup = InlineKeyboardMarkup(rows)
    if old != markup:
        from telegram.error import BadRequest
        try:
            edited = await bot.edit_message_reply_markup(
                chat_id=channel.id, message_id=ledger.message_id, reply_markup=markup,
            )
        except BadRequest as exc:
            if "message is not modified" not in str(exc).lower():
                raise
        else:
            if not edited or edited.reply_markup != markup:
                raise PreviewRecoveryError("keyboard_verification_failed")
    await audit.record_event("preview.keyboard_restored", review_id=review_id,
                             actor=str(actor), idempotency_key=key,
                             detail={"message_id": ledger.message_id})
    return {**result, "status": "restored", "url": url}


async def _published_txt(row):
    """Original is immutable; editorial selection comes only from published_snapshot."""
    title = row["title"] or ""
    original = json.loads(row["media_json"] or "[]") + json.loads(row["documents_json"] or "[]")
    selected = original
    revision_id = row["published_source_revision_id"]
    if revision_id:
        async with get_db() as conn:
            revision = await (await conn.execute(
                "SELECT * FROM editorial_revisions WHERE id=? AND review_id=? "
                "AND status='published'", (revision_id, row["id"]),
            )).fetchone()
        if revision is None:
            raise PreviewRecoveryError("published_snapshot_missing")
        snapshot = Snapshot.from_dict(json.loads(revision["published_snapshot"] or "{}"))
        title = snapshot.title
        selected = [original[i] for i in snapshot.ordered_indexes() if 0 <= i < len(original)]
    items = [MediaItem.file_id("document", item["file_id"], filename=item["filename"])
             for item in selected if item.get("filename", "").lower().endswith(".txt")]
    if not items:
        raise PreviewRecoveryError("published_txt_missing")
    return title, items
