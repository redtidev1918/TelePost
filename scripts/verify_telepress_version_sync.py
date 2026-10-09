#!/usr/bin/env python3
"""Enforce the shared telepress version contract across repositories.

TelePost consumes the ``telepress`` Python package to publish the readable
Telegraph page, and the TelePress service image runs the same package. These
pins must never drift: TelePost uses features such as
``publish_rich_markdown`` that only exist in newer releases, and the pagination
that decides how a long novel is split across Telegraph pages lives in the same
package.

* source of truth: ``requirements.txt`` in this repo
* must match, in redtidev1918/pixivflow-telepost-deploy:
  * ``docker/telepress.Dockerfile``      - the standalone TelePress service
  * ``docker/telepost.Dockerfile``       - TelePress layered over the TelePost image

Both deploy files are checked because they are two names for one dependency.
Historically only the first was watched, so the overlay default silently stayed
on 0.17.0 through two TelePress releases; anything built from that overlay kept
the old paginator. A pin nobody checks is a pin that drifts.

Exit 0 when every pin matches, 1 with a clear message otherwise.
"""
from __future__ import annotations

import re
import sys
import urllib.request
from pathlib import Path

REQUIREMENTS = Path(__file__).resolve().parent.parent / "requirements.txt"

RAW_BASE = (
    "https://raw.githubusercontent.com/redtidev1918/"
    "pixivflow-telepost-deploy/main/"
)

# path -> pattern whose first group is the pinned version
DEPLOY_PINS = {
    "docker/telepress.Dockerfile": re.compile(
        r"telepress(?:\[[^\]]+\])?==([0-9][0-9.]*)"
    ),
    "docker/telepost.Dockerfile": re.compile(
        r"ARG\s+TELEPRESS_VERSION=([0-9][0-9.]*)"
    ),
}


def parse_requirements(text: str) -> str | None:
    match = re.search(r"^telepress==([0-9][0-9.]*)$", text, re.MULTILINE)
    return match.group(1) if match else None


def fetch(path: str) -> str:
    request = urllib.request.Request(
        RAW_BASE + path,
        headers={"User-Agent": "telepost-version-sync-check"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8", "replace")


def main() -> int:
    local = parse_requirements(REQUIREMENTS.read_text(encoding="utf-8"))
    if not local:
        print("requirements.txt has no telepress== pin", file=sys.stderr)
        return 1

    drift: list[str] = []
    for path, pattern in DEPLOY_PINS.items():
        try:
            match = pattern.search(fetch(path))
        except Exception as exc:  # noqa: BLE001 - CI should fail loudly on drift
            print(f"cannot read deploy pin from {path}: {exc}", file=sys.stderr)
            return 1
        if not match:
            print(f"{path} has no telepress version pin", file=sys.stderr)
            return 1
        remote = match.group(1)
        print(f"{path}: {remote}")
        if remote != local:
            drift.append(f"  {path} = {remote}")

    if drift:
        print(
            f"telepress version drift: TelePost requirements.txt = {local}, "
            "deploy repository has:",
            file=sys.stderr,
        )
        for line in drift:
            print(line, file=sys.stderr)
        return 1

    print(f"telepress version contract OK (TelePost and {len(DEPLOY_PINS)} deploy pins = {local})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
