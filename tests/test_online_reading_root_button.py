"""在线阅读 = 频道 root 纯展示属性（§online-reading）。

取代旧的「裸链 + LinkPreviewOptions Instant View」方案。新设计：

* 在线阅读是频道 root 消息的一个 inline keyboard 按钮，绝不产生第二条消息；
* 它永远不改变 Publication topology（item 数 / batch 数 / 相册打包 / caption
  归属 / reply mode / discussion 路由 / ledger / idempotency / outcome）；
* 正式频道 footer 不再出现 READ_ONLINE 超链接；
* 溢出（overflow）/ 讨论区回复 / 后续 batch 绝不继承 root navigation；
* Preview 失败 / 超时 / 禁用 / 不适用 一律不阻塞 Publication，也不产生按钮。

测试矩阵按 (preview state × root capability × reply mode × surface) 正交覆盖。
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from telepost.application.publication import (
    PublicationService,
    PublishCommand,
    channel_caption,
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
from telepost.domain.navigation import (
    READ_ONLINE_ACTION,
    READ_ONLINE_LABEL,
    NavigationItem,
    read_online_navigation,
)
from telepost.domain.novel_preview import PreviewStatus
from telepost.domain.presentation_policy import (
    PreviewState,
    PublicationSurface,
    build_publication_presentation,
)
from telepost.telegram.delivery.planner import plan_delivery
from telepost.telegram.delivery.sender import PTBSender

PREVIEW_URL = "https://telegra.ph/readonline-iv-01-01"


# ---- fakes / fixtures ------------------------------------------------------

class _Enricher:
    """生产资格镜像：默认只要注入就成功（测试 album 时也用它）。"""

    def __init__(self, url=PREVIEW_URL):
        self._url = url

    async def enrich(self, **kwargs):
        return SimpleNamespace(
            status=PreviewStatus.SUCCEEDED, succeeded=True, url=self._url
        )


class _FailingEnricher:
    """Preview 以非成功状态结束（FAILED / TIMEOUT / DISABLED / NOT_APPLICABLE）。"""

    def __init__(self, status=PreviewStatus.FAILED, url=""):
        self._status = status
        self._url = url

    async def enrich(self, **kwargs):
        return SimpleNamespace(
            status=self._status, succeeded=(self._status is PreviewStatus.SUCCEEDED),
            url=self._url,
        )


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
    from database import db_manager
    from telepost.storage.sqlite.ledger import DeliveryLedgerRepository
    monkeypatch.setattr(db_manager, "DB_PATH", str(tmp_path / "ledger.db"))
    await db_manager.init_db()
    return DeliveryLedgerRepository()


def _txt_item(tmp_path, name="novel.txt"):
    path = tmp_path / name
    path.write_text("第一章 正文", encoding="utf-8")
    return MediaItem.local("document", str(path), name)


def _command(items, key="readonline:test:1", **overrides):
    base = dict(
        chat_id="@channel",
        items=list(items),
        caption_data={"tags": "#novel", "title": "标题"},
        user_id=7,
        idempotency_key=key,
        target_id="bot1-novel",
        work_type="novel",
        work_id="12345",
    )
    base.update(overrides)
    return PublishCommand(**base)


def _service(delivery, ledger, enricher=None):
    return PublicationService(
        delivery=delivery, ledger=ledger, link_builder=lambda mid: f"/{mid}",
        novel_preview=enricher,
    )


def _read_online_button(request: DeliveryRequest):
    for nav in request.root_navigation:
        if nav.action == READ_ONLINE_ACTION:
            return nav
    return None


# ---- presentation policy (pure) --------------------------------------------

@pytest.mark.parametrize("state", [
    PreviewState.SUCCEEDED,
])
def test_channel_single_root_gets_read_online_button(state):
    pres = build_publication_presentation(
        preview_state=state, preview_url=PREVIEW_URL,
        surface=PublicationSurface.CHANNEL_PUBLICATION, root_supports_button=True,
    )
    assert [n.action for n in pres.root_navigation] == [READ_ONLINE_ACTION]
    assert pres.root_navigation[0].url == PREVIEW_URL
    # footer 不再包含 READ_ONLINE（按钮承载）
    assert all(n.action != READ_ONLINE_ACTION for n in pres.footer_navigation)


@pytest.mark.parametrize("state", [
    PreviewState.NOT_APPLICABLE,
    PreviewState.DISABLED,
    PreviewState.FAILED,
    PreviewState.TIMEOUT,
])
def test_non_success_states_never_produce_button(state):
    pres = build_publication_presentation(
        preview_state=state, preview_url=PREVIEW_URL,
        surface=PublicationSurface.CHANNEL_PUBLICATION, root_supports_button=True,
    )
    assert pres.root_navigation == []


def test_succeeded_with_invalid_url_has_no_button():
    pres = build_publication_presentation(
        preview_state=PreviewState.SUCCEEDED, preview_url="telegra.ph/x",
        surface=PublicationSurface.CHANNEL_PUBLICATION, root_supports_button=True,
    )
    assert pres.root_navigation == []


def test_album_root_cannot_take_button_presentation_fallback():
    """专辑 root（不支持 reply_markup）即便 preview 成功也不挂按钮（§presentation-fallback）。"""
    pres = build_publication_presentation(
        preview_state=PreviewState.SUCCEEDED, preview_url=PREVIEW_URL,
        surface=PublicationSurface.CHANNEL_PUBLICATION, root_supports_button=False,
    )
    assert pres.root_navigation == []


def test_review_surface_keeps_read_online_footer_link():
    """审核/预览面保留 READ_ONLINE footer 超链接（不是按钮）。"""
    pres = build_publication_presentation(
        preview_state=PreviewState.SUCCEEDED, preview_url=PREVIEW_URL,
        surface=PublicationSurface.REVIEW, root_supports_button=True,
    )
    assert [n.action for n in pres.root_navigation] == []
    assert any(n.action == READ_ONLINE_ACTION for n in pres.footer_navigation)


def test_notification_surface_has_no_cta_at_all():
    pres = build_publication_presentation(
        preview_state=PreviewState.SUCCEEDED, preview_url=PREVIEW_URL,
        surface=PublicationSurface.NOTIFICATION, root_supports_button=True,
    )
    assert pres.root_navigation == []
    assert pres.footer_navigation == []


def test_channel_footer_has_submission_cta_but_no_read_online():
    pres = build_publication_presentation(
        preview_state=PreviewState.SUCCEEDED, preview_url=PREVIEW_URL,
        surface=PublicationSurface.CHANNEL_PUBLICATION, root_supports_button=True,
        channel_footer_link="https://t.me/xgdPost_bot", miniapp_submit_cta=True,
    )
    footer_actions = [n.action for n in pres.footer_navigation]
    assert READ_ONLINE_ACTION not in footer_actions
    assert "BOT_SUBMIT" in footer_actions
    assert "MINI_APP_SUBMIT" in footer_actions


def test_policy_is_pure_and_does_not_change_topology():
    """Presentation policy 不改 item / batch / routing —— 它只返回导航放置。"""
    import copy
    pres1 = build_publication_presentation(
        preview_state=PreviewState.SUCCEEDED, preview_url=PREVIEW_URL,
        surface=PublicationSurface.CHANNEL_PUBLICATION, root_supports_button=True,
    )
    pres2 = build_publication_presentation(
        preview_state=PreviewState.SUCCEEDED, preview_url=PREVIEW_URL,
        surface=PublicationSurface.CHANNEL_PUBLICATION, root_supports_button=True,
    )
    assert pres1 == pres2
    # 返回的导航项是不可变的纯值对象
    assert isinstance(pres1.root_navigation[0], NavigationItem)


# ---- navigation adapter (the only PTB-object builder) -----------------------

def test_navigation_markup_builds_single_button_row():
    sender = PTBSender(bot=object(), chat_id=1)
    markup = sender._navigation_markup([
        NavigationItem(READ_ONLINE_ACTION, READ_ONLINE_LABEL, PREVIEW_URL),
    ])
    rows = markup.inline_keyboard
    assert len(rows) == 1
    assert rows[0][0].text == READ_ONLINE_LABEL
    assert rows[0][0].url == PREVIEW_URL


def test_navigation_markup_empty_is_none():
    sender = PTBSender(bot=object(), chat_id=1)
    assert sender._navigation_markup([]) is None


def test_sender_text_branch_no_longer_emits_link_preview_options():
    sender = PTBSender(bot=object(), chat_id=1)
    item = MediaItem(MediaKind.TEXT, SubmissionText("plain caption"))
    kw = sender._single_kwargs(item, None)
    assert kw["method"] == "send_message"
    assert kw["disable_web_page_preview"] is True
    assert "link_preview_options" not in kw


def test_sender_media_branch_untouched_by_navigation():
    sender = PTBSender(bot=object(), chat_id=1)
    kw = sender._single_kwargs(MediaItem.file_id("photo", "P1"), "cap")
    assert kw["method"] == "send_photo"
    assert "link_preview_options" not in kw
    assert "disable_web_page_preview" not in kw


# ---- channel caption: no READ_ONLINE footer on channel publication ---------

def test_channel_caption_suppresses_read_online_footer_when_requested(monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "CHANNEL_FOOTER_LINK", "")
    caption = channel_caption(
        {"tags": "#novel", "title": "标题", "novel_preview_url": PREVIEW_URL},
        include_readonline_footer=False,
    )
    assert "📖 在线阅读" not in caption
    assert PREVIEW_URL not in caption


# ---- DeliveryRequest model carries root_navigation -------------------------

def test_delivery_request_defaults_root_navigation_empty():
    req = DeliveryRequest(chat_id="@c", items=[MediaItem.file_id("photo", "P")])
    assert req.root_navigation == []


# ---- application layer: single TXT novel + preview success ----------------

@pytest.mark.asyncio
async def test_single_txt_preview_success_attaches_button_no_extra_message(
        ledger, tmp_path):
    delivery = _RecordingDelivery()
    service = _service(delivery, ledger, _Enricher())

    outcome = await service.publish(_command([_txt_item(tmp_path)]))

    assert outcome.status == "published"
    request = delivery.last
    # 关键：不再追加 TEXT(bare link) 消息 —— root 仍是单个 TXT 文档
    assert [i.kind for i in request.items] == [MediaKind.DOCUMENT]
    assert all(i.kind is not MediaKind.TEXT for i in request.items)
    # caption 完整且留在 root 上
    assert request.caption is not None
    assert "标题" in request.caption
    assert PREVIEW_URL not in request.caption
    assert "📖 在线阅读" not in request.caption
    # 按钮只出现在 root navigation，且指向 Telegraph URL
    btn = _read_online_button(request)
    assert btn is not None
    assert btn.url == PREVIEW_URL


@pytest.mark.asyncio
async def test_single_txt_preview_failed_has_no_button(ledger, tmp_path):
    delivery = _RecordingDelivery()
    service = _service(delivery, ledger, _FailingEnricher(PreviewStatus.FAILED))

    outcome = await service.publish(_command([_txt_item(tmp_path)]))

    assert outcome.status == "published"
    request = delivery.last
    assert request.items == [_txt_item(tmp_path)] or [
        i.kind for i in request.items] == [MediaKind.DOCUMENT]
    assert _read_online_button(request) is None
    assert request.caption is not None


@pytest.mark.asyncio
async def test_single_txt_preview_timeout_has_no_button(ledger, tmp_path):
    delivery = _RecordingDelivery()
    service = _service(delivery, ledger, _FailingEnricher(PreviewStatus.TIMEOUT))

    outcome = await service.publish(_command([_txt_item(tmp_path)]))

    assert outcome.status == "published"
    assert _read_online_button(delivery.last) is None


@pytest.mark.asyncio
async def test_single_txt_preview_disabled_has_no_button(ledger, tmp_path):
    delivery = _RecordingDelivery()
    service = _service(delivery, ledger, _FailingEnricher(PreviewStatus.DISABLED))

    outcome = await service.publish(_command([_txt_item(tmp_path)]))

    assert outcome.status == "published"
    assert _read_online_button(delivery.last) is None


@pytest.mark.asyncio
async def test_single_photo_root_with_preview_url_gets_button(ledger):
    """root 是单张图片（封面）且 preview 成功 → 图片 root 挂按钮。"""
    delivery = _RecordingDelivery()
    service = _service(delivery, ledger, _Enricher())

    outcome = await service.publish(_command([MediaItem.file_id("photo", "COVER")]))

    assert outcome.status == "published"
    request = delivery.last
    assert [i.kind for i in request.items] == [MediaKind.PHOTO]
    assert _read_online_button(request) is not None


@pytest.mark.asyncio
async def test_multi_image_album_preview_never_repacks(ledger):
    """多图专辑不因 preview/按钮触发 repack / split / append text。"""
    delivery = _RecordingDelivery()
    service = _service(delivery, ledger, _Enricher())

    items = [MediaItem.file_id("photo", f"P{i}") for i in range(1, 4)]
    outcome = await service.publish(_command(items))

    assert outcome.status == "published"
    request = delivery.last
    # 拓扑不变：仍是 3 张图，无 TEXT 追加
    assert [i.kind for i in request.items] == [MediaKind.PHOTO, MediaKind.PHOTO, MediaKind.PHOTO]
    assert all(i.kind is not MediaKind.TEXT for i in request.items)
    # 专辑 root 不支持按钮（presentation fallback）
    assert _read_online_button(request) is None


@pytest.mark.parametrize("reply_mode", ["chain", "post", "discussion"])
@pytest.mark.asyncio
async def test_single_txt_preview_matrix_attaches_button_on_root(ledger, tmp_path,
                                                                reply_mode):
    """单文档 + preview：无论外部 reply mode，root 都是 DOC(caption) + 按钮，无 TEXT。"""
    delivery = _RecordingDelivery()
    service = _service(delivery, ledger, _Enricher())
    command = _command([_txt_item(tmp_path)], reply_mode=ReplyMode(reply_mode))

    outcome = await service.publish(command)

    assert outcome.status == "published"
    request = delivery.last
    assert request.reply_mode.value == reply_mode
    assert [i.kind for i in request.items] == [MediaKind.DOCUMENT]
    assert all(i.kind is not MediaKind.TEXT for i in request.items)
    assert _read_online_button(request) is not None
    assert request.caption is not None
    assert "📖 在线阅读" not in request.caption


# ---- 2.76.0 regression: caption must never migrate into discussion --------

@pytest.mark.asyncio
async def test_single_novel_preview_never_moves_caption_into_discussion(
        ledger, tmp_path):
    """回归：单 TXT + preview 成功 + discussion 不得再产生 document + TEXT，
    caption 必须留在 root（频道），不得搬去 discussion。"""
    delivery = _RecordingDelivery()
    service = _service(delivery, ledger, _Enricher())
    command = _command([_txt_item(tmp_path)], reply_mode=ReplyMode.DISCUSSION)

    outcome = await service.publish(command)

    assert outcome.status == "published"
    request = delivery.last
    # 核心不变量：root 仍是单个 TXT，没有第二条 TEXT 消息
    assert [i.kind for i in request.items] == [MediaKind.DOCUMENT]
    assert all(i.kind is not MediaKind.TEXT for i in request.items)
    # caption 完整留在 root
    assert request.caption is not None and "标题" in request.caption
    assert PREVIEW_URL not in request.caption
    # 按钮作为 root 展示属性存在
    assert _read_online_button(request) is not None


# ---- no-duplicate CTA invariant -------------------------------------------

@pytest.mark.asyncio
async def test_read_online_appears_exactly_once_as_root_button(ledger, tmp_path):
    delivery = _RecordingDelivery()
    service = _service(delivery, ledger, _Enricher())
    outcome = await service.publish(_command([_txt_item(tmp_path)]))
    assert outcome.status == "published"
    request = delivery.last
    # root 恰好一个 READ_ONLINE（来自 inline keyboard）
    buttons = [n for n in request.root_navigation if n.action == READ_ONLINE_ACTION]
    assert len(buttons) == 1
    # footer 不再有 READ_ONLINE
    assert "https://telegra.ph" not in request.caption
    assert "📖 在线阅读" not in request.caption
    # 没有 bare URL 的独立消息
    assert all(i.kind is not MediaKind.TEXT for i in request.items)


# ---- executor: root-only reply_markup -------------------------------------

@pytest.mark.asyncio
async def test_executor_attaches_markup_only_to_root():
    from telepost.telegram.delivery.executor import execute_plan

    class _FakeSender:
        def __init__(self):
            self.calls = []

        async def send_album(self, batch, *, reply_to, caption, reply_markup=None):
            self.calls.append(("album", reply_markup))
            return [DeliveredMessage(chat_id=1, message_id=len(self.calls),
                                    kind=item.kind) for item in batch.items]

        async def send_single(self, item, *, reply_to, caption, reply_markup=None):
            self.calls.append(("single", reply_markup))
            return DeliveredMessage(chat_id=1, message_id=len(self.calls), kind=item.kind)

    markup = object()
    sender = _FakeSender()
    plan = plan_delivery([
        MediaItem.file_id("photo", "P1"),
        MediaItem.local("document", "/tmp/x.txt", "x.txt"),
    ])
    await execute_plan(plan, sender, root_reply_markup=markup)
    # 第一个消息（root）拿到 markup，第二个没有
    assert sender.calls[0] == ("single", markup)
    assert sender.calls[1] == ("single", None)


@pytest.mark.asyncio
async def test_executor_drops_markup_for_album_root():
    from telepost.telegram.delivery.executor import execute_plan

    class _FakeSender:
        def __init__(self):
            self.calls = []

        async def send_album(self, batch, *, reply_to, caption, reply_markup=None):
            self.calls.append(reply_markup)
            return [DeliveredMessage(chat_id=1, message_id=len(self.calls),
                                    kind=item.kind) for item in batch.items]

        async def send_single(self, item, *, reply_to, caption, reply_markup=None):
            self.calls.append(reply_markup)
            return DeliveredMessage(chat_id=1, message_id=len(self.calls), kind=item.kind)

    markup = object()
    sender = _FakeSender()
    plan = plan_delivery([
        MediaItem.file_id("photo", "P1"),
        MediaItem.file_id("photo", "P2"),
    ])
    await execute_plan(plan, sender, root_reply_markup=markup)
    # 专辑 root 不能挂 reply_markup —— 必须丢弃
    assert sender.calls == [None]


# ---- sender: real bot call receives reply_markup --------------------------

@pytest.mark.asyncio
async def test_sender_passes_reply_markup_to_bot():
    bot = AsyncMock()
    sender = PTBSender(bot, 1, timeouts={})
    markup = sender._navigation_markup([
        NavigationItem(READ_ONLINE_ACTION, READ_ONLINE_LABEL, PREVIEW_URL),
    ])
    item = MediaItem.file_id("document", "F1")
    await sender.send_single(item, reply_to=None, caption="cap", reply_markup=markup)
    kwargs = bot.send_document.await_args.kwargs
    assert kwargs["reply_markup"] is markup
    assert kwargs["caption"] == "cap"
