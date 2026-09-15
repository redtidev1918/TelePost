"""Publication Presentation Cleanup (§publication-presentation).

Regression matrix for two presentation invariants:

1. Media actions reflect the REAL attachment kinds: photo / video / animation
   are previewable; document (and audio) are not. A document-only publication
   must never show the "点击查看" media-view hint, and mixed publications keep it.

2. Only an explicit human submitter (submitter_user_id / submitter_username)
   may be presented as 投稿人. Actor, API token name, credential holder, source
   and legacy request identity are never authorship. Anonymous hides the public
   submitter without erasing ownership; service submissions (submitter NULL)
   have no author at all. Internal surfaces may show 来源 (provenance) instead.
"""
import pytest

from telepost.domain import presentation


class TestPreviewableKinds:
    @pytest.mark.parametrize("kind", ["photo", "video", "animation"])
    def test_previewable(self, kind):
        assert presentation.is_previewable_media(kind) is True

    @pytest.mark.parametrize("kind", ["document", "audio", "", "unknown"])
    def test_not_previewable(self, kind):
        assert presentation.is_previewable_media(kind) is False

    def test_normalizes_dicts_and_objects(self):
        assert presentation.is_previewable_media({"type": "PHOTO"}) is True
        assert presentation.is_previewable_media({"kind": "video"}) is True
        assert presentation.is_previewable_media(type("M", (), {
            "kind": "animation"})()) is True
        assert presentation.is_previewable_media({"type": "document"}) is False

    def test_has_previewable_media_matrix(self):
        assert presentation.has_previewable_media(["document"]) is False
        assert presentation.has_previewable_media(["document", "document"]) is False
        assert presentation.has_previewable_media(["photo"]) is True
        assert presentation.has_previewable_media(["photo", "document"]) is True
        assert presentation.has_previewable_media(["video", "document"]) is True
        assert presentation.has_previewable_media(["audio"]) is False
        assert presentation.has_previewable_media([]) is False
        assert presentation.has_previewable_media(None) is False
        assert presentation.has_previewable_media("animation") is True

    def test_media_kinds_from_items(self):
        assert presentation.media_kinds_from_items([
            {"type": "photo", "file_id": "a"}, {"kind": "document", "file_id": "b"},
        ]) == ["photo", "document"]
        assert presentation.media_kinds_from_items(
            ["video:x", "audio:y"]
        ) == ["video", "audio"]
        assert presentation.media_kinds_from_items(None) == []


def _caption(data, surface="channel"):
    from utils.helper_functions import build_caption
    import utils.helper_functions as hf
    hf.SHOW_SUBMITTER = True
    base = {
        "tags": "#t", "title": "标题", "note": "内容",
        "spoiler": "true", "anonymous": "false",
    }
    return build_caption({**base, **data}, surface=surface)


class TestDocumentOnlyMediaHint:
    def test_document_only_hides_hint(self):
        assert "点击查看" not in _caption({"media_types": ["document"]})

    def test_multiple_documents_only_hides_hint(self):
        assert "点击查看" not in _caption({"media_types": ["document", "document"]})

    def test_no_attachments_hides_hint(self):
        assert "点击查看" not in _caption({"media_types": []})
        assert "点击查看" not in _caption({})

    def test_audio_only_hides_hint(self):
        assert "点击查看" not in _caption({"media_types": ["audio"]})

    def test_photo_only_keeps_hint(self):
        assert "点击查看" in _caption({"media_types": ["photo"]})

    def test_photo_plus_document_keeps_hint(self):
        assert "点击查看" in _caption({"media_types": ["photo", "document"]})

    def test_video_plus_document_keeps_hint(self):
        assert "点击查看" in _caption({"media_types": ["video", "document"]})

    def test_spoiler_false_never_hints(self):
        base = {"media_types": ["photo"], "spoiler": "false"}
        assert "点击查看" not in _caption(base)


class TestSubmitterIdentity:
    def test_human_shows_explicit_submitter(self):
        caption = _caption({
            "media_types": ["photo"],
            "submitter_user_id": 42, "submitter_username": "alice",
        })
        assert '投稿人：<a href="tg://user?id=42">@alice</a>' in caption

    def test_human_without_username_uses_display_name(self):
        caption = _caption({
            "submitter_user_id": 42,
            "submitter_username": "",
            "submitter_display_name": "Alice Zhang",
        })
        assert '投稿人：<a href="tg://user?id=42">Alice Zhang</a>' in caption
        assert "user42" not in caption

    def test_anonymous_human_hides_submitter(self):
        caption = _caption({
            "submitter_user_id": 42, "submitter_username": "alice",
            "anonymous": "true",
        })
        assert "投稿人" not in caption

    @pytest.mark.parametrize("alias", ["submit_Token", "TELEPOST_BOT1_SUBMIT_TOKEN"])
    def test_service_token_alias_never_presented_as_submitter(self, alias):
        # The API path passes user_id/username = token identity; submitter is
        # NULL. The token name must not surface as the author anywhere.
        caption = _caption({
            "media_types": ["photo"],
            "user_id": 5073758941, "username": alias,
            "submitter_user_id": None, "submitter_username": "",
            "source": "api",
        })
        assert "投稿人" not in caption
        assert alias not in caption

    def test_legacy_request_identity_is_not_a_submitter(self):
        # Old rows only carry user_id/username: no explicit human submitter →
        # nothing is shown, even with SHOW_SUBMITTER on.
        caption = _caption({"user_id": 42, "username": "someuser"})
        assert "投稿人" not in caption

    def test_service_review_surface_shows_source_not_submitter(self):
        caption = _caption({
            "media_types": ["document"],
            "user_id": 1, "username": "submit_Token",
            "submitter_user_id": None, "source": "api",
            "source_label": "PixivFlow",
        }, surface="review")
        assert "投稿人" not in caption
        assert "来源：PixivFlow" in caption
        assert "submit_Token" not in caption

    def test_service_plain_api_review_source_label(self):
        caption = _caption({
            "user_id": 1, "username": "submit_Token",
            "submitter_user_id": None, "source": "api",
        }, surface="review")
        assert "来源：API" in caption

    def test_public_channel_never_shows_source(self):
        caption = _caption({
            "user_id": 1, "username": "submit_Token",
            "submitter_user_id": None, "source": "api",
            "source_label": "PixivFlow",
        }, surface="channel")
        assert "来源" not in caption
        assert "投稿人" not in caption

    def test_miniapp_surface_hides_submitter_and_source(self):
        caption = _caption({
            "user_id": 1, "username": "submit_Token",
            "submitter_user_id": None, "source": "api",
        }, surface="miniapp")
        assert "投稿人" not in caption
        assert "来源" not in caption

    def test_system_surface_always_hides_identity(self):
        caption = _caption({
            "submitter_user_id": 42, "submitter_username": "alice",
            "source": "api",
        }, surface="system")
        assert "投稿人" not in caption
        assert "来源" not in caption


class TestReviewCardIdentity:
    def _command(self, **overrides):
        from telepost.application.review_queue import QueueCommand
        return QueueCommand(
            user_id=7, username="submit_Token", tags="#t", title="标题",
            note="", link="", anonymous=False, spoiler=False, source="api",
            source_label="PixivFlow", **overrides,
        )

    def test_service_card_has_no_submitter_line(self):
        from telepost.telegram.review_keyboard import control_text
        text = control_text(review_id=9, command=self._command(),
                            media_count=0, document_count=2)
        assert "投稿人" not in text
        assert "投稿方式：HTTP API" in text
        assert "PixivFlow" in text

    def test_human_card_shows_submitter(self):
        from telepost.telegram.review_keyboard import control_text
        command = self._command(submitter_user_id=12345, submitter_username="alice")
        text = control_text(review_id=9, command=command,
                            media_count=2, document_count=0)
        assert "投稿人：alice" in text

    def test_review_caption_service_shows_source_not_submitter(self):
        from telepost.application.review_queue import _caption_from_command
        caption = _caption_from_command(self._command(),
                                        media=[], documents=[{"type": "document"}])
        assert "投稿人" not in caption
        assert "来源：PixivFlow" in caption

    def test_review_caption_human_shows_submitter(self):
        from telepost.application.review_queue import _caption_from_command
        caption = _caption_from_command(self._command(
            submitter_user_id=12345, submitter_username="alice"),
            media=[{"type": "photo", "file_id": "a"}])
        assert "投稿人：alice" in caption
        assert "tg://" not in caption


class TestPublicationServiceKinds:
    """The channel publication derives media kinds from the REAL delivery items."""

    def _caption_via_command(self, kinds):
        from types import SimpleNamespace
        from telepost.application.publication import PublicationService
        command = SimpleNamespace(
            caption_data={"tags": "#t", "title": "T", "spoiler": "true",
                          "anonymous": "false", "user_id": 1},
            items=[SimpleNamespace(kind=k) for k in kinds],
        )
        return PublicationService._caption(command)

    def test_document_only_no_hint_on_channel(self):
        assert "点击查看" not in self._caption_via_command(["document", "document"])

    def test_mixed_keeps_hint_on_channel(self):
        assert "点击查看" in self._caption_via_command(["photo", "document"])

    def test_audio_only_no_hint(self):
        assert "点击查看" not in self._caption_via_command(["audio"])
