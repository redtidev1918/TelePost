"""
匿名投稿测试（caption 脱名 + 预览页开关）
"""
from types import SimpleNamespace

from utils.helper_functions import build_caption


def _row(**overrides):
    row = {
        "user_id": 42,
        "username": "someuser",
        "tags": "#测试",
        "title": "标题",
        "note": "简介",
        "link": "",
        "spoiler": "false",
        "anonymous": "false",
    }
    row.update(overrides)
    return row


class TestAnonymousCaption:
    def test_non_anonymous_shows_submitter(self):
        caption = build_caption(_row())
        assert "投稿人" in caption and "someuser" in caption

    def test_anonymous_hides_submitter(self):
        caption = build_caption(_row(anonymous="true"))
        assert "投稿人" not in caption
        assert "someuser" not in caption

    def test_anonymous_missing_column_defaults_visible(self):
        row = _row()
        del row["anonymous"]
        caption = build_caption(row)
        assert "投稿人" in caption


class TestPreviewSwitches:
    def test_keyboard_labels_follow_state(self):
        from handlers.preview_handlers import _build_preview_keyboard

        kb_off = _build_preview_keyboard({"anonymous": "false", "spoiler": "false"})
        labels = [b.text for row in kb_off.inline_keyboard for b in row]
        assert "🕵️ 匿名：关" in labels
        assert "🔞 剧透：关" in labels

        kb_on = _build_preview_keyboard({"anonymous": "true", "spoiler": "true"})
        labels2 = [b.text for row in kb_on.inline_keyboard for b in row]
        assert "🕵️ 匿名：开" in labels2
        assert "🔞 剧透：开" in labels2
