#!/usr/bin/env python3
"""Scenario tests for the incremental-CI change policy.

Runs representative change sets through the real classifier
(.github/actions/detect-changes/classify.py) against the real policy
(.github/ci-policy.json) and asserts the expected category flags, so policy
edits cannot silently re-scope CI gating. Pure stdlib, no pytest dependency;
also executed as a self-check step inside the Mini App CI changes job.

Usage:
    python scripts/test_ci_policy.py [--policy PATH] [--classify PATH]
"""

from __future__ import annotations

import argparse
import importlib.util
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent

# (name, changed files, expected {flag: bool}); unlisted flags default to False.
SCENARIOS = [
    (
        "readme-only PR",
        ["README.md", "README.en.md"],
        {"docs": True, "docs_only": True, "any": True},
    ),
    (
        "docs-only merge (PR #343 shape)",
        ["README.md", "README.en.md", "docs/INSTALL.md", "docs/en/INSTALL.md"],
        {"docs": True, "docs_only": True, "any": True},
    ),
    (
        "docsite assets and english docs",
        ["docs/assets/screenshots/bot-menu.jpg", "docs/en/COMMANDS.md"],
        {"docs": True, "docs_only": True, "any": True},
    ),
    (
        "python-only change",
        ["telepost/domain/packing.py", "tests/test_packing.py"],
        {"backend": True, "any": True},
    ),
    (
        "root python entrypoint",
        ["main.py"],
        {"backend": True, "any": True},
    ),
    (
        "python dependency change",
        ["requirements-dev.txt"],
        {"backend": True, "any": True},
    ),
    (
        "webapp-only change",
        ["webapp/src/pages/Home.tsx", "webapp/package-lock.json", "webapp/e2e/submit.spec.ts"],
        {"frontend": True, "any": True},
    ),
    (
        "shared API contract change",
        ["api/openapi.yaml"],
        {"integration": True, "any": True},
    ),
    (
        "deploy config change",
        ["docker-compose.yml", "install.sh"],
        {"deploy": True, "any": True},
    ),
    (
        "ci workflow change",
        [".github/workflows/miniapp-ci.yml", ".github/ci-policy.json"],
        {"ci": True, "any": True},
    ),
    (
        "release-please PR shape",
        ["CHANGELOG.md", "pyproject.toml"],
        {"docs": True, "backend": True, "any": True},
    ),
    (
        "mixed docs + frontend",
        ["webapp/src/api/client.ts", "README.md"],
        {"docs": True, "frontend": True, "any": True},
    ),
    (
        "unclassified file (conservative)",
        ["LICENSE"],
        {"unclassified": True, "any": True},
    ),
    (
        "empty change set",
        [],
        {"any": False, "docs_only": False, "unclassified": False},
    ),
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", default=str(ROOT / ".github" / "ci-policy.json"))
    parser.add_argument(
        "--classify", default=str(ROOT / ".github" / "actions" / "detect-changes" / "classify.py")
    )
    args = parser.parse_args()

    spec = importlib.util.spec_from_file_location("classify", args.classify)
    assert spec is not None and spec.loader is not None
    classify = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(classify)

    policy = classify.load_policy(args.policy)
    failures = 0
    for name, files, expected in SCENARIOS:
        result = classify.classify_files(files, policy)
        problems = []
        for flag, want in expected.items():
            if bool(result.get(flag, False)) != want:
                problems.append(f"{flag}: want {want}, got {result.get(flag)}")
        for flag in result:
            if flag not in expected and bool(result[flag]):
                problems.append(f"{flag}: unexpectedly true")
        if problems:
            failures += 1
            print(f"FAIL {name}: {'; '.join(problems)}")
        else:
            print(f"ok   {name}")
    if failures:
        print(f"\n{failures} scenario(s) failed")
        return 1
    print(f"\nall {len(SCENARIOS)} scenarios passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
