"""TelePress media-proxy env projection (production incident hardening).

The in-process TelePress library only reads ``TELEPRESS_MEDIA_PROXY_BASE`` /
``TELEPRESS_MEDIA_PROXY_HOSTS``. TelePost's single source of truth is
``MEDIA_PROXY_BASE_URL`` / ``MEDIA_PROXY_HOSTS`` (shared with the delivery
planner); ``build_telepress_provider`` must project them so the rich-novel
proxy rewrite can never be silently skipped again — the incident that
published every online-reading novel page without images.
"""
import os

import pytest

import config.settings as settings
from telepost.application import telepress_provider as tp


@pytest.fixture
def proxy_env(monkeypatch):
    for key in ("TELEPRESS_MEDIA_PROXY_BASE", "TELEPRESS_MEDIA_PROXY_HOSTS"):
        monkeypatch.delenv(key, raising=False)
    return monkeypatch


def test_projects_telepost_media_proxy_config(proxy_env):
    proxy_env.setattr(settings, "MEDIA_PROXY_BASE_URL", "https://proxy.example.com")
    proxy_env.setattr(
        settings, "MEDIA_PROXY_HOSTS", frozenset({"z.pximg.net", "i.pximg.net"})
    )
    tp._project_media_proxy_env()
    assert os.environ["TELEPRESS_MEDIA_PROXY_BASE"] == "https://proxy.example.com"
    # deterministic: sorted, comma-joined
    assert os.environ["TELEPRESS_MEDIA_PROXY_HOSTS"] == "i.pximg.net,z.pximg.net"


def test_explicit_telepress_env_wins(proxy_env):
    proxy_env.setattr(settings, "MEDIA_PROXY_BASE_URL", "https://proxy.example.com")
    proxy_env.setattr(settings, "MEDIA_PROXY_HOSTS", frozenset({"i.pximg.net"}))
    proxy_env.setenv("TELEPRESS_MEDIA_PROXY_BASE", "https://explicit.example.com")
    proxy_env.setenv("TELEPRESS_MEDIA_PROXY_HOSTS", "img.other.example")
    tp._project_media_proxy_env()
    assert os.environ["TELEPRESS_MEDIA_PROXY_BASE"] == "https://explicit.example.com"
    assert os.environ["TELEPRESS_MEDIA_PROXY_HOSTS"] == "img.other.example"


def test_empty_telepost_config_leaves_env_unset(proxy_env):
    proxy_env.setattr(settings, "MEDIA_PROXY_BASE_URL", "")
    proxy_env.setattr(settings, "MEDIA_PROXY_HOSTS", frozenset())
    tp._project_media_proxy_env()
    assert "TELEPRESS_MEDIA_PROXY_BASE" not in os.environ
    assert "TELEPRESS_MEDIA_PROXY_HOSTS" not in os.environ


def test_build_provider_projects_env(proxy_env):
    proxy_env.setattr(settings, "MEDIA_PROXY_BASE_URL", "https://proxy.example.com")
    proxy_env.setattr(settings, "MEDIA_PROXY_HOSTS", frozenset({"i.pximg.net"}))
    provider = tp.build_telepress_provider(
        "telegraph-token", client_factory=lambda token: object()
    )
    assert provider is not None
    assert os.environ["TELEPRESS_MEDIA_PROXY_BASE"] == "https://proxy.example.com"
    assert os.environ["TELEPRESS_MEDIA_PROXY_HOSTS"] == "i.pximg.net"
