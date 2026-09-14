"""Ghost mention contract (§ghost-mention).

Telegram turns ``<a href="tg://user?id=...">@name</a>`` (parse_mode=HTML) into
a ``text_mention`` entity, which notifies that user — even when the message is
only a review preview that later gets superseded and deleted. The stale "@"
badge then survives in the client. Review and system surfaces must therefore
never emit a notification-capable entity; display identity is plain text.
"""
from utils.helper_functions import build_caption
from telepost.application.review_queue import QueueCommand, _caption_from_command


def _assert_no_mention_entity(text: str) -> None:
    lowered = text.lower()
    assert "tg://" not in lowered
    assert "<a " not in lowered
    assert "</a>" not in lowered
    # An @-anchor is what a client turns into a mention suggestion.
    assert "@" not in text


def test_channel_caption_has_no_mention_entity(monkeypatch):
    monkeypatch.setattr("utils.helper_functions.SHOW_SUBMITTER", True)
    caption = build_caption({
        "tags": "#tag", "title": "标题", "note": "备注", "link": "",
        "anonymous": "false", "spoiler": "false",
        "user_id": 12345, "username": "alice",
    }, surface="channel")
    assert "投稿人：alice" in caption
    _assert_no_mention_entity(caption)


def test_review_caption_has_no_mention_entity(monkeypatch):
    monkeypatch.setattr("utils.helper_functions.SHOW_SUBMITTER", True)
    command = QueueCommand(
        user_id=12345, username="alice", tags="#tag", title="标题",
        note="", link="", anonymous=False, spoiler=False, source="api",
        submitter_user_id=12345, submitter_username="alice",
    )
    caption = _caption_from_command(command)
    assert "投稿人：alice" in caption
    _assert_no_mention_entity(caption)


def test_service_submission_never_mentions_token_owner(monkeypatch):
    """A service submission must not @ the API-token owner (§identity)."""
    monkeypatch.setattr("utils.helper_functions.SHOW_SUBMITTER", True)
    command = QueueCommand(
        user_id=5073758941, username="pixivflow", tags="#tag", title="",
        note="", link="", anonymous=False, spoiler=False, source="api",
    )
    caption = _caption_from_command(command)
    _assert_no_mention_entity(caption)
    assert "投稿人" not in caption
    assert "5073758941" not in caption  # no raw id either


def test_anonymous_submission_hides_submitter(monkeypatch):
    monkeypatch.setattr("utils.helper_functions.SHOW_SUBMITTER", True)
    caption = build_caption({
        "tags": "#tag", "title": "", "note": "", "link": "",
        "anonymous": "true", "spoiler": "false",
        "user_id": 12345, "username": "alice",
    }, surface="review")
    assert "投稿人" not in caption
    _assert_no_mention_entity(caption)


def test_html_in_username_stays_escaped(monkeypatch):
    monkeypatch.setattr("utils.helper_functions.SHOW_SUBMITTER", True)
    caption = build_caption({
        "tags": "#tag", "title": "", "note": "", "link": "",
        "anonymous": "false", "spoiler": "false",
        "user_id": 1, "username": "<b>evil</b>",
    }, surface="review")
    assert "<b>" not in caption
    _assert_no_mention_entity(caption)