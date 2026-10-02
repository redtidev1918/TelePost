"""频道小说帖「在线阅读」Instant View 预览（§readonline-iv）兼容矩阵。

Covered here:

* helper ``with_readonline_preview`` — bare-link block append / no-op;
* application layer (``PublicationService.publish``):
  - 单 TXT + preview → caption 迁移为尾随 TEXT，裸链结尾，``link_preview_url`` 就位；
  - 多文档 + preview → 既有尾随形态不回归，末尾追加裸链块；
  - 无 preview（图集 / 预览失败 / 单文档）→ 与改动前逐字节一致；
  - ``READONLINE_LINK_PREVIEW=false`` → 全链路旧行为；
* dict 往返（``_item_to_dict`` / ``_items_from_dicts``）不丢 ``link_preview_url``；
* 遗留 chat 直发辅助 ``_chat_delivery_with_readonline`` 三态；
* 发送层 ``PTBSender._single_kwargs`` TEXT 分支显式预览开关，其它分支不动；
* ``channel_caption``（审核/预览面共用）永不出现裸链块。
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from database import db_manager
from telepost.application.publication import (
    PublicationService,
    PublishCommand,
    channel_caption,
    with_readonline_preview,
)
from telepost.domain.delivery import (
    DeliveryRequest,
    DeliveryResult,
    DeliveredMessage,
    MediaItem,
    MediaKind,
    ReplyMode,
    SubmissionText,
)
from telepost.storage.sqlite.ledger import DeliveryLedgerRepository

PREVIEW_URL = "https://telegra.ph/readonline-iv-01-01"


# ---- fakes / fixtures ------------------------------------------------------

class _Enricher:
    """Mirrors production eligibility: only a TXT-novel snapshot can succeed."""

    def __init__(self, url=PREVIEW_URL):
        self._url = url

    async def enrich(self, **kwargs):
        items = kwargs.get("items") or []
        has_txt = any(
            (i.filename or "").lower().endswith(".txt") for i in items
        )
        url = self._url if has_txt else ""
        return SimpleNamespace(succeeded=bool(url), url=url)


class _RecordingDelivery:
    def __init__(self):
        self.requests = []

    @property
    def last(self) -> DeliveryRequest:
        return self.requests[-1]

    async def deliver(self, request: DeliveryRequest) -> DeliveryResult:
        self.requests.append(request)
        return DeliveryResult.delivered([
            DeliveredMessage(
                chat_id=request.chat_id, message_id=1000 + len(self.requests),
                kind=request.items[0].kind, file_id="doc-1",
            )
        ])


@pytest.fixture
async def ledger(monkeypatch, tmp_path):
    monkeypatch.setattr(db_manager, "DB_PATH", str(tmp_path / "ledger.db"))
    await db_manager.init_db()
    return DeliveryLedgerRepository()


def _txt_item(tmp_path, name="novel.txt"):
    path = tmp_path / name
    path.write_text("第一章 正文", encoding="utf-8")
    return MediaItem.local("document", str(path), name)


def _command(items, key="readonline:test:1"):
    return PublishCommand(
        chat_id="@channel",
        items=list(items),
        caption_data={"tags": "#novel", "title": "标题"},
        user_id=7,
        idempotency_key=key,
        target_id="bot1-novel",
        work_type="novel",
        work_id="12345",
    )


def _service(delivery, ledger, enricher=None):
    return PublicationService(
        delivery=delivery, ledger=ledger, link_builder=lambda mid: f"/{mid}",
        novel_preview=enricher,
    )


def _text_items(request):
    return [i for i in request.items if i.kind is MediaKind.TEXT]


# ---- helper ----------------------------------------------------------------

def test_with_readonline_preview_appends_bare_link_block():
    out = with_readonline_preview("正文 caption", PREVIEW_URL)
    assert out == f"正文 caption\n\n📖 在线阅读\n{PREVIEW_URL}"


def test_with_readonline_preview_without_url_is_noop():
    assert with_readonline_preview("正文 caption", "") == "正文 caption"
    assert with_readonline_preview("正文 caption", None) == "正文 caption"


# ---- application layer: compat matrix --------------------------------------

@pytest.mark.asyncio
async def test_single_txt_with_preview_keeps_caption_on_document_plus_bare_link(
        ledger, tmp_path):
    delivery = _RecordingDelivery()
    service = _service(delivery, ledger, _Enricher())

    outcome = await service.publish(_command([_txt_item(tmp_path)]))

    assert outcome.status == "published"
    request = delivery.last
    assert [i.kind for i in request.items] == [MediaKind.DOCUMENT, MediaKind.TEXT]
    source = _text_items(request)[0].source
    assert isinstance(source, SubmissionText)
    assert source.text == f"📖 在线阅读\n{PREVIEW_URL}"
    assert source.link_preview_url == PREVIEW_URL
    # Root caption stays on the file, 2.75.0 form preserved.
    assert request.caption is not None
    assert PREVIEW_URL not in request.caption
    assert "在线阅读" not in request.caption


@pytest.mark.asyncio
async def test_multi_document_with_preview_tails_caption_then_bare_link(
        ledger, tmp_path):
    delivery = _RecordingDelivery()
    service = _service(delivery, ledger, _Enricher())
    command = _command([
        _txt_item(tmp_path, "novel.txt"), _txt_item(tmp_path, "extra.txt"),
    ])

    outcome = await service.publish(command)

    assert outcome.status == "published"
    request = delivery.last
    assert request.caption is None
    assert [i.kind for i in request.items] == [
        MediaKind.DOCUMENT, MediaKind.DOCUMENT, MediaKind.TEXT, MediaKind.TEXT,
    ]
    caption_item, bare_item = _text_items(request)
    assert caption_item.source.link_preview_url is None
    assert PREVIEW_URL not in caption_item.source.text
    assert "标题" in caption_item.source.text
    assert bare_item.source.text == f"📖 在线阅读\n{PREVIEW_URL}"
    assert bare_item.source.link_preview_url == PREVIEW_URL


@pytest.mark.asyncio
async def test_multi_document_without_preview_is_byte_identical(ledger, tmp_path):
    """既有尾随形态：无 preview 时文本与改动前逐字节一致。"""
    delivery = _RecordingDelivery()
    service = _service(delivery, ledger, _Enricher(url=""))
    command = _command([
        _txt_item(tmp_path, "novel.txt"), _txt_item(tmp_path, "extra.txt"),
    ])

    outcome = await service.publish(command)

    assert outcome.status == "published"
    request = delivery.last
    assert request.caption is None
    source = _text_items(request)[0].source
    assert source.link_preview_url is None
    assert source.text == PublicationService._caption(command)


@pytest.mark.asyncio
async def test_single_document_without_preview_keeps_caption_on_document(
        ledger, tmp_path):
    """预览失败/缺失的单文档投稿：caption 仍骑文档消息，逐字节一致。"""
    delivery = _RecordingDelivery()
    service = _service(delivery, ledger, _Enricher(url=""))
    command = _command([_txt_item(tmp_path)])

    outcome = await service.publish(command)

    assert outcome.status == "published"
    request = delivery.last
    assert [i.kind for i in request.items] == [MediaKind.DOCUMENT]
    assert request.caption == PublicationService._caption(command)
    assert request.caption is not None


@pytest.mark.asyncio
async def test_photo_submission_form_is_unchanged(ledger):
    delivery = _RecordingDelivery()
    service = _service(delivery, ledger, _Enricher())
    command = _command([MediaItem.file_id("photo", "P1")])

    outcome = await service.publish(command)

    assert outcome.status == "published"
    request = delivery.last
    assert [i.kind for i in request.items] == [MediaKind.PHOTO]
    assert _text_items(request) == []
    assert request.caption == PublicationService._caption(command)


@pytest.mark.asyncio
async def test_switch_off_restores_previous_behavior(ledger, tmp_path,
                                                     monkeypatch):
    """READONLINE_LINK_PREVIEW=false：有 preview 也走改动前形态。"""
    monkeypatch.setattr("config.settings.READONLINE_LINK_PREVIEW", False)
    delivery = _RecordingDelivery()
    service = _service(delivery, ledger, _Enricher())
    command = _command([_txt_item(tmp_path)])

    outcome = await service.publish(command)

    assert outcome.status == "published"
    request = delivery.last
    assert [i.kind for i in request.items] == [MediaKind.DOCUMENT]
    assert _text_items(request) == []
    assert request.caption is not None
    assert "在线阅读" in request.caption


# ---- dict round-trip (legacy delivery seam) ---------------------------------

def test_dict_round_trip_preserves_link_preview_url():
    from handlers.publish import _item_to_dict, _items_from_dicts

    item = MediaItem(MediaKind.TEXT, SubmissionText(
        "caption\n\n📖 在线阅读\n" + PREVIEW_URL,
        link_preview_url=PREVIEW_URL,
    ))
    restored = _items_from_dicts([_item_to_dict(item)])[0]
    assert restored.source.text == item.source.text
    assert restored.source.link_preview_url == PREVIEW_URL


def test_dict_round_trip_without_preview_url_stays_none():
    from handlers.publish import _item_to_dict, _items_from_dicts

    item = MediaItem(MediaKind.TEXT, SubmissionText("plain caption"))
    data = _item_to_dict(item)
    assert "link_preview_url" not in data
    restored = _items_from_dicts([data])[0]
    assert restored.source.link_preview_url is None


# ---- legacy chat direct-publish helper --------------------------------------

def test_legacy_chat_helper_single_document_keeps_caption_plus_bare_item():
    from handlers.publish import _chat_delivery_with_readonline

    items, caption = _chat_delivery_with_readonline(
        [{"kind": "document", "file_id": "F", "filename": "novel.txt"}],
        "caption body", PREVIEW_URL,
        caption_data={"tags": "#novel", "title": "标题", "novel_preview_url": PREVIEW_URL},
    )
    assert caption is not None
    assert "标题" in caption
    assert "在线阅读" not in caption
    assert items[0]["kind"] == "document"
    assert items[-1]["kind"] == "text"
    assert items[-1]["text"] == f"📖 在线阅读\n{PREVIEW_URL}"
    assert items[-1]["link_preview_url"] == PREVIEW_URL


def test_legacy_chat_helper_without_preview_is_passthrough():
    from handlers.publish import _chat_delivery_with_readonline

    base = [{"kind": "photo", "file_id": "P1"}]
    items, caption = _chat_delivery_with_readonline(base, "caption body", "")
    assert items is base
    assert caption == "caption body"


def test_legacy_chat_helper_switch_off_is_passthrough(monkeypatch):
    from handlers.publish import _chat_delivery_with_readonline

    monkeypatch.setattr("config.settings.READONLINE_LINK_PREVIEW", False)
    base = [{"kind": "document", "file_id": "F"}]
    items, caption = _chat_delivery_with_readonline(base, "caption body",
                                                    PREVIEW_URL)
    assert items is base
    assert caption == "caption body"


# ---- phase matrix: reply mode x document count x preview ----------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("reply_mode", [
    "chain", "post", "discussion",
])
async def test_single_document_preview_matrix_is_boarded_identically_at_app_layer(
        ledger, tmp_path, reply_mode):
    """单文档 + preview：无论外部 reply mode，应用层都保持 DOC(caption) + TEXT(裸链)。"""
    delivery = _RecordingDelivery()
    service = _service(delivery, ledger, _Enricher())
    command = _command([_txt_item(tmp_path)])
    command = PublishCommand(
        chat_id=command.chat_id,
        items=command.items,
        caption_data=command.caption_data,
        user_id=command.user_id,
        idempotency_key=command.idempotency_key,
        target_id=command.target_id,
        work_type=command.work_type,
        work_id=command.work_id,
        reply_mode=ReplyMode(reply_mode),
    )

    outcome = await service.publish(command)

    assert outcome.status == "published"
    request = delivery.last
    assert request.reply_mode.value == reply_mode
    assert [i.kind for i in request.items] == [MediaKind.DOCUMENT, MediaKind.TEXT]
    bare = _text_items(request)[0]
    assert bare.source.text == f"📖 在线阅读\nhttps://telegra.ph/readonline-iv-01-01"
    assert bare.source.link_preview_url == PREVIEW_URL
    assert request.caption is not None
    assert "在线阅读" not in request.caption


@pytest.mark.asyncio
@pytest.mark.parametrize("reply_mode", [
    "chain", "post", "discussion",
])
async def test_multi_document_preview_matrix_is_boarded_identically_at_app_layer(
        ledger, tmp_path, reply_mode):
    """多文档 + preview：应用层保持 DOC 组、尾随 caption、再尾随两行裸链。"""
    delivery = _RecordingDelivery()
    service = _service(delivery, ledger, _Enricher())
    command = _command([
        _txt_item(tmp_path, "novel.txt"), _txt_item(tmp_path, "extra.txt"),
    ])
    command = PublishCommand(
        chat_id=command.chat_id,
        items=command.items,
        caption_data=command.caption_data,
        user_id=command.user_id,
        idempotency_key=command.idempotency_key,
        target_id=command.target_id,
        work_type=command.work_type,
        work_id=command.work_id,
        reply_mode=ReplyMode(reply_mode),
    )

    outcome = await service.publish(command)

    assert outcome.status == "published"
    request = delivery.last
    assert request.reply_mode.value == reply_mode
    assert [i.kind for i in request.items] == [
        MediaKind.DOCUMENT, MediaKind.DOCUMENT, MediaKind.TEXT, MediaKind.TEXT,
    ]
    caption_item, bare_item = _text_items(request)
    assert caption_item.source.link_preview_url is None
    assert bare_item.source.text == f"📖 在线阅读\nhttps://telegra.ph/readonline-iv-01-01"
    assert bare_item.source.link_preview_url == PREVIEW_URL
    assert request.caption is None


# ---- discussion routing: readonline trailing text must land in comments -------

class _Msg:
    def __init__(self, message_id, chat_id=None):
        self.message_id = message_id
        if chat_id is not None:
            self.chat = SimpleNamespace(id=chat_id)


@pytest.mark.asyncio
async def test_discussion_single_document_preview_routes_bare_link_to_comments(
        monkeypatch):
    from handlers import publish

    bot = AsyncMock()
    bot.get_chat.return_value = SimpleNamespace(id=-1001, linked_chat_id=-1002)
    bot.send_document.return_value = _Msg(1, -1001)
    bot.send_message.return_value = _Msg(2, -1002)
    waiter = AsyncMock(return_value=(-1002, 77))
    monkeypatch.setattr(publish, "_wait_for_discussion_forward", waiter)

    sent, main = await publish.deliver_items_to_chat(
        bot,
        -1001,
        [
            {"kind": "document", "file_id": "F", "filename": "novel.txt"},
            {
                "kind": "text",
                "text": f"📖 在线阅读\nhttps://telegra.ph/readonline-iv-01-01",
                "link_preview_url": PREVIEW_URL,
            },
        ],
        caption="完整 caption（无在线阅读 footer）",
        timeout_kwargs={},
        reply_mode="discussion",
    )

    assert publish._channel_message_ids(sent, main) == [1]
    doc_kw = bot.send_document.await_args.kwargs
    assert doc_kw["chat_id"] == -1001
    assert doc_kw["caption"] == "完整 caption（无在线阅读 footer）"
    msg_kw = bot.send_message.await_args.kwargs
    assert msg_kw["chat_id"] == -1002
    assert msg_kw["reply_to_message_id"] == 77
    assert msg_kw["text"] == f"📖 在线阅读\nhttps://telegra.ph/readonline-iv-01-01"
    options = msg_kw["link_preview_options"]
    assert options.url == PREVIEW_URL
    assert options.prefer_large_media is True


@pytest.mark.asyncio
async def test_discussion_multi_document_preview_routes_caption_and_bare_link_to_comments(
        monkeypatch):
    from handlers import publish

    bot = AsyncMock()
    bot.get_chat.return_value = SimpleNamespace(id=-1001, linked_chat_id=-1002)
    bot.send_media_group.return_value = [_Msg(1, -1001), _Msg(2, -1001)]
    bot.send_message.side_effect = [_Msg(3, -1002), _Msg(4, -1002)]
    waiter = AsyncMock(return_value=(-1002, 77))
    monkeypatch.setattr(publish, "_wait_for_discussion_forward", waiter)

    sent, main = await publish.deliver_items_to_chat(
        bot,
        -1001,
        [
            {"kind": "document", "file_id": "F1", "filename": "novel.txt"},
            {"kind": "document", "file_id": "F2", "filename": "extra.txt"},
            {"kind": "text", "text": "完整 caption（无 footer 在线阅读）"},
            {
                "kind": "text",
                "text": f"📖 在线阅读\nhttps://telegra.ph/readonline-iv-01-01",
                "link_preview_url": PREVIEW_URL,
            },
        ],
        caption=None,
        timeout_kwargs={},
        reply_mode="discussion",
    )

    assert publish._channel_message_ids(sent, main) == [1, 2]
    assert bot.send_media_group.await_args.kwargs["chat_id"] == -1001
    assert bot.send_message.await_args_list[0].kwargs["chat_id"] == -1002
    assert bot.send_message.await_args_list[0].kwargs["text"] == "完整 caption（无 footer 在线阅读）"
    options = bot.send_message.await_args_list[1].kwargs["link_preview_options"]
    assert options.url == PREVIEW_URL
    assert options.prefer_large_media is True

# ---- send layer --------------------------------------------------------------

def _sender():
    from telepost.telegram.delivery.sender import PTBSender
    return PTBSender(bot=object(), chat_id=1)


def test_sender_text_with_preview_url_emits_link_preview_options():
    item = MediaItem(MediaKind.TEXT, SubmissionText(
        "caption\n\n📖 在线阅读\n" + PREVIEW_URL,
        link_preview_url=PREVIEW_URL,
    ))
    kw = _sender()._single_kwargs(item, None)

    assert kw["method"] == "send_message"
    options = kw["link_preview_options"]
    assert options.url == PREVIEW_URL
    assert options.prefer_large_media is True
    assert "disable_web_page_preview" not in kw


def test_sender_text_without_preview_url_keeps_preview_disabled():
    item = MediaItem(MediaKind.TEXT, SubmissionText("plain caption"))
    kw = _sender()._single_kwargs(item, None)

    assert kw["disable_web_page_preview"] is True
    assert "link_preview_options" not in kw


def test_sender_media_branches_untouched():
    kw = _sender()._single_kwargs(MediaItem.file_id("photo", "P1"), "cap")
    assert kw["method"] == "send_photo"
    assert "link_preview_options" not in kw
    assert "disable_web_page_preview" not in kw


# ---- review / preview surfaces ----------------------------------------------

def test_channel_caption_never_contains_bare_link_block():
    """channel_caption 本体不改：审核/预览面共用它，绝不出现裸链块。"""
    caption = channel_caption({
        "tags": "#novel", "title": "标题", "novel_preview_url": PREVIEW_URL,
    })
    assert f"📖 在线阅读\n{PREVIEW_URL}" not in caption
    assert not caption.rstrip().endswith(PREVIEW_URL)
