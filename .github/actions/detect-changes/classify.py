#!/usr/bin/env python3
"""Classify changed files against a declarative category policy.

Generic mechanism, no project specifics: reads a JSON policy mapping
``category -> [globs]``, matches every changed file against every category
(a file may belong to several), and emits boolean flags plus two derived
safety flags:

``docs_only``
    Every changed file matched only the ``docs`` category.
``unclassified``
    At least one changed file matched no category. Callers must treat this
    as "run the full suite" (conservative fallback), never as a skip.

Globs are anchored to the full path and support ``**`` (any depth, including
zero), ``*`` (within one segment), ``?`` and character classes. Deleted and
renamed paths count as changes. Keys starting with ``_`` in the policy are
documentation, not categories.

Outputs are written to ``$GITHUB_OUTPUT`` (or stdout with ``--dry-run``):
``files`` (newline list, capped), ``count``, ``any``, ``docs_only``,
``unclassified`` and one boolean per category. An empty change set yields
``any=false`` with every category false.

Usage:
    python3 classify.py --policy .github/ci-policy.json --files changed.txt
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

MAX_FILES_OUTPUT = 200


def glob_to_regex(pattern: str) -> re.Pattern[str]:
    """Compile a path glob with ``**`` support into an anchored regex."""
    parts = ["^"]
    i = 0
    length = len(pattern)
    while i < length:
        ch = pattern[i]
        if ch == "*":
            if pattern[i : i + 2] == "**":
                while i < length and pattern[i] == "*":
                    i += 1
                if i < length and pattern[i] == "/":
                    parts.append("(?:.*/)?")
                    i += 1
                else:
                    parts.append(".*")
                continue
            parts.append("[^/]*")
            i += 1
        elif ch == "?":
            parts.append("[^/]")
            i += 1
        elif ch == "[":
            end = pattern.find("]", i + 1)
            if end == -1:
                parts.append(re.escape(ch))
                i += 1
            else:
                inner = pattern[i + 1 : end]
                if inner.startswith("!"):
                    inner = "^" + inner[1:]
                parts.append("[" + inner + "]")
                i = end + 1
        else:
            parts.append(re.escape(ch))
            i += 1
    parts.append("$")
    return re.compile("".join(parts))


def load_policy(path: str) -> dict[str, list[re.Pattern[str]]]:
    with open(path, encoding="utf-8") as fh:
        raw = json.load(fh)
    policy: dict[str, list[re.Pattern[str]]] = {}
    for category, globs in raw.items():
        if category.startswith("_") or not isinstance(globs, list):
            continue
        policy[category] = [glob_to_regex(g) for g in globs]
    return policy


def classify_files(
    files: list[str], policy: dict[str, list[re.Pattern[str]]]
) -> dict[str, object]:
    categories = sorted(policy)
    hits = {c: False for c in categories}
    docs_only = bool(files)
    unclassified = False
    for path in files:
        matched = [c for c in categories if any(rx.match(path) for rx in policy[c])]
        if not matched:
            unclassified = True
            docs_only = False
            continue
        for c in matched:
            hits[c] = True
        if any(c != "docs" for c in matched):
            docs_only = False
    result: dict[str, object] = dict(hits)
    result["any"] = bool(files)
    result["docs_only"] = docs_only
    result["unclassified"] = unclassified
    return result


def write_outputs(result: dict[str, object], files: list[str], dry_run: bool) -> None:
    lines = [f"{key}={'true' if value else 'false'}" for key, value in sorted(result.items())]
    shown = files[:MAX_FILES_OUTPUT]
    payload = lines + [f"count={len(files)}"]
    if dry_run:
        for entry in shown:
            print(f"  {entry}", file=sys.stderr)
        for entry in payload:
            print(entry)
        return
    output_path = os.environ.get("GITHUB_OUTPUT", "")
    fh = open(output_path, "a", encoding="utf-8", newline="\n") if output_path else sys.stdout
    with fh:
        for entry in payload:
            print(entry, file=fh)
        print("files<<__CHANGED_FILES__", file=fh)
        for entry in shown:
            print(entry, file=fh)
        print("__CHANGED_FILES__", file=fh)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", required=True, help="policy JSON path")
    parser.add_argument("--files", required=True, help="file with one changed path per line")
    parser.add_argument("--dry-run", action="store_true", help="print outputs, skip GITHUB_OUTPUT")
    args = parser.parse_args()

    with open(args.files, encoding="utf-8") as fh:
        files = [line.rstrip("\n") for line in fh if line.strip()]
    policy = load_policy(args.policy)
    result = classify_files(files, policy)
    write_outputs(result, files, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
