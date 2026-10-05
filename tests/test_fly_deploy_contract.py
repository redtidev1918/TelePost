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
