"""Deployment contract: fly.toml must keep the review-group refetch endpoint.

The production machine env comes from ``fly.toml [env]`` plus Fly secrets.
``PIXIVFLOW_REFETCH_BASE_URL`` is public and lives in the config; losing it makes
every "重抓" click answer "PixivFlow 重抓服务未配置" (refetch.py gate) while the
secret token alone is not enough. This test guards that the deploy config
declares the endpoint and never commits the shared secret.
"""
from pathlib import Path

import tomllib

FLY_TOML = Path(__file__).resolve().parent.parent / "fly.toml"
EXPECTED_REFETCH_BASE_URL = "https://pixivflow-scheduler.fly.dev"


def _env() -> dict:
    with FLY_TOML.open("rb") as file:
        return tomllib.load(file)["env"]


def test_fly_toml_declares_refetch_base_url():
    env = _env()
    assert env.get("PIXIVFLOW_REFETCH_BASE_URL") == EXPECTED_REFETCH_BASE_URL


def test_fly_toml_never_commits_refetch_token():
    # The token is a shared Fly secret, never a config env value. The comment
    # text may name the variable; the env section must not contain it.
    env = _env()
    assert "PIXIVFLOW_REFETCH_TOKEN" not in env


EXPECTED_MEDIA_PROXY_BASE_URL = "https://pixiv-media-proxy.redtidev1918.workers.dev"


def test_fly_toml_declares_media_proxy_pair():
    env = _env()
    assert env.get("MEDIA_PROXY_BASE_URL") == EXPECTED_MEDIA_PROXY_BASE_URL
    assert "i.pximg.net" in env.get("MEDIA_PROXY_HOSTS", "")
    # TelePress reads its own names; the bridge maps from the TelePost pair, so
    # both must be present and consistent to keep 在线阅读 images proxied.
    assert env.get("TELEPRESS_MEDIA_PROXY_BASE") == EXPECTED_MEDIA_PROXY_BASE_URL
    assert "i.pximg.net" in env.get("TELEPRESS_MEDIA_PROXY_HOSTS", "")
