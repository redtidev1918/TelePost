from telepost.application.delivery_planner import (
    MediaPlanEntry,
    plan_review_media,
)


def _entry(source_url="https://i.pximg.net/img/1.jpg", strategy="remote_url"):
    return {"index": 0, "kind": "image", "strategy": strategy, "source_url": source_url}


def test_media_proxy_rewrites_exact_allowed_host(monkeypatch):
    monkeypatch.setattr("config.settings.MEDIA_PROXY_BASE_URL", "https://media.example")
    monkeypatch.setattr("config.settings.MEDIA_PROXY_HOSTS", frozenset({"i.pximg.net"}))
    plan = plan_review_media([], [_entry()])
    assert plan.entries[0].source_url == "https://media.example/media/i.pximg.net/img/1.jpg"
    assert plan.to_media_items()[0].source.url.endswith("/img/1.jpg")


def test_media_proxy_leaves_unknown_hosts_unchanged(monkeypatch):
    monkeypatch.setattr("config.settings.MEDIA_PROXY_BASE_URL", "https://media.example")
    monkeypatch.setattr("config.settings.MEDIA_PROXY_HOSTS", frozenset({"i.pximg.net"}))
    url = "https://example.org/img/1.jpg"
    plan = plan_review_media([], [_entry(url)])
    assert plan.entries[0].source_url == url


def test_media_proxy_disabled_keeps_source_url(monkeypatch):
    monkeypatch.setattr("config.settings.MEDIA_PROXY_BASE_URL", "")
    monkeypatch.setattr("config.settings.MEDIA_PROXY_HOSTS", frozenset())
    url = "https://i.pximg.net/img/1.jpg"
    plan = plan_review_media([], [_entry(url)])
    assert plan.entries[0].source_url == url
