#!/usr/bin/env python3
"""Enforce the shared telepress version contract across repositories.

TelePost consumes the ``telepress`` Python package to publish the readable
Telegraph page, and the TelePress service image runs the same package. These
two pins must never drift: TelePost uses features such as
``publish_rich_markdown`` that only exist in newer releases.

* source of truth: ``requirements.txt`` in this repo
* must match: ``docker/telepress.Dockerfile`` in redtidev1918/pixivflow-telepost-deploy

Exit 0 when they match, 1 with a clear message otherwise.
"""
from __future__ import annotations

import re
import sys
import urllib.request
from pathlib import Path

REQUIREMENTS = Path(__file__).resolve().parent.parent / "requirements.txt"
DEPLOY_DOCKERFILE_URL = (
    "https://raw.githubusercontent.com/redtidev1918/"
    "pixivflow-telepost-deploy/main/docker/telepress.Dockerfile"
)


def parse_requirements(text: str) -> str | None:
    match = re.search(r"^telepress==([0-9][0-9.]*)$", text, re.MULTILINE)
    return match.group(1) if match else None


def parse_deploy_pin(text: str) -> str | None:
    match = re.search(
        r"telepress(?:\[[^\]]+\])?==([0-9][0-9.]*)",
        text,
    )
    return match.group(1) if match else None


def main() -> int:
    local = parse_requirements(REQUIREMENTS.read_text(encoding="utf-8"))
    if not local:
        print("requirements.txt has no telepress== pin", file=sys.stderr)
        return 1
    try:
        request = urllib.request.Request(
            DEPLOY_DOCKERFILE_URL,
            headers={"User-Agent": "telepost-version-sync-check"},
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            remote = parse_deploy_pin(response.read().decode("utf-8", "replace"))
    except Exception as exc:  # noqa: BLE001 - CI should fail loudly on drift
        print(f"cannot read deploy telepress pin: {exc}", file=sys.stderr)
        return 1
    if not remote:
        print("deploy telepress.Dockerfile has no telepress== pin", file=sys.stderr)
        return 1
    if local != remote:
        print(
            f"telepress version drift: TelePost={local} "
            f"TelePress service={remote}",
            file=sys.stderr,
        )
        return 1
    print(f"telepress version contract OK (both {local})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
