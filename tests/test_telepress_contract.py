"""Execute the installed TelePress API, mocking only external Telegraph I/O.

CI runs this against both supported versions, so permissive FakePublisher kwargs
cannot hide signature or rendering regressions.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from telepost.application.telepress_provider import TelePressNovelPreviewPublisher
from telepost.domain.novel_preview import NovelSnapshot


@pytest.mark.asyncio
@pytest.mark.parametrize("rich", [False, True])
async def test_installed_telepress_text_and_rich_author_contract(monkeypatch, rich):
    import telepress
    from telepress.core import TelegraphPublisher
    client = Mock()
    client.create_page.return_value = {"url": "https://telegra.ph/Example-11-02", "path": "Example-11-02"}
    monkeypatch.setattr("telepress.core.TelegraphAuth", lambda: SimpleNamespace(
        get_client=lambda *a: client))
    monkeypatch.setenv("TELEPRESS_MEDIA_PROXY_BASE", "https://media.example.org")
    monkeypatch.setenv("TELEPRESS_MEDIA_PROXY_HOSTS", "images.example.org")
    publisher = TelegraphPublisher("test", skip_duplicate=False)
    bot = SimpleNamespace(get_me=AsyncMock(return_value=SimpleNamespace(username="ExampleBot")))
    adapter = TelePressNovelPreviewPublisher("test",client_factory=lambda token:publisher,bot=bot)
    snapshot = NovelSnapshot(title="Example",content="Body",rich_content="Body\n\n![](images/1.jpg)" if rich else "",
                             media_manifest=({"local":"images/1.jpg","sourceUrl":"https://images.example.org/1.jpg"},) if rich else ())
    result = await adapter.publish_preview(snapshot)
    assert result.succeeded, f"TelePress {telepress.__version__}: {result.reason}"
    assert result.rich == rich
    client.create_page.assert_called_once()
    kwargs = client.create_page.call_args.kwargs
    assert kwargs["author_name"] == "@ExampleBot"
    assert kwargs["author_url"] == "https://t.me/ExampleBot"
    assert kwargs["title"] == "Example"


@pytest.mark.asyncio
@pytest.mark.parametrize("rich", [False, True])
async def test_installed_telepress_never_repeats_uncertain_create(monkeypatch, rich):
    from telepress.core import TelegraphPublisher
    client = Mock()
    client.create_page.side_effect = TimeoutError("response lost")
    monkeypatch.setattr("telepress.core.TelegraphAuth", lambda: SimpleNamespace(
        get_client=lambda *a: client))
    monkeypatch.setattr("telepress.core.time.sleep", lambda _seconds: None)
    monkeypatch.setenv("TELEPRESS_MEDIA_PROXY_BASE", "https://media.example.org")
    monkeypatch.setenv("TELEPRESS_MEDIA_PROXY_HOSTS", "images.example.org")
    publisher = TelegraphPublisher("test", skip_duplicate=False)
    adapter = TelePressNovelPreviewPublisher("test", client_factory=lambda token: publisher)
    snapshot = NovelSnapshot(
        title="Example", content="Body",
        rich_content="Body\n\n![](images/1.jpg)" if rich else "",
        media_manifest=({"local": "images/1.jpg", "sourceUrl": "https://images.example.org/1.jpg"},) if rich else (),
    )
    result = await adapter.publish_preview(snapshot)
    assert not result.succeeded
    client.create_page.assert_called_once()
