#!/usr/bin/env python3
"""Generate docs/download.md from the latest GitHub release.

Auto-run by .github/workflows/update-download-page.yml on every `release published`
event so the page always points at the newest assets. Idempotent: no release change
means no diff, so it won't spam commits.
"""
import json
import os
import re
import subprocess
import sys

REPO = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("GITHUB_REPOSITORY", "")


def api(url: str) -> dict:
    return json.loads(subprocess.check_output(["gh", "api", url]).decode())


def infer_repo() -> str:
    out = subprocess.check_output(["git", "remote", "get-url", "origin"]).decode().strip()
    m = re.search(r"(?:github\.com[:/])([^/]+)/([^/.]+)", out)
    return f"{m.group(1)}/{m.group(2)}" if m else ""


def platform_of(fn: str) -> tuple:
    """Best-effort platform label from the asset filename."""
    f = fn.lower()
    if "windows" in f or f.endswith(".exe") or ".msi" in f:
        os_name = "Windows"
    elif "darwin" in f or "macos" in f:
        os_name = "macOS"
    elif "linux" in f or f.endswith(".deb") or f.endswith(".appimage"):
        os_name = "Linux"
    elif f.endswith(".apk"):
        os_name = "Android"
    else:
        os_name = "通用"
    arch = ""
    for a in ("arm64", "aarch64", "amd64", "x86_64", "x64", "arm", "386", "x86"):
        if a in f:
            arch = a
            break
    if arch == "x86_64":
        arch = "x64"
    return os_name, arch


def main() -> int:
    global REPO
    if not REPO:
        REPO = infer_repo()
    if not REPO or "/" not in REPO:
        print("cannot determine owner/repo", file=sys.stderr)
        return 1

    try:
        rel = api(f"repos/{REPO}/releases/latest")
    except subprocess.CalledProcessError:
        print("no latest release", file=sys.stderr)
        return 1

    tag = rel.get("tag_name", "")
    name = rel.get("name") or tag
    published = (rel.get("published_at") or "")[:10]
    assets = rel.get("assets", [])
    project = REPO.split("/")[1]

    lines = [
        f"# 📥 下载 {project}",
        "",
        "本页由 GitHub Actions 在每次发版时**自动更新**，始终指向最新 Release。",
        "",
        f"## 最新版本：`{tag}`（{published}）",
        "",
        f"👉 [查看 Release 说明与校验和]({rel.get('html_url', '')})",
        "",
    ]

    # 可选的手写预览段：docs/download-preview.md（稳定文件，不被生成器覆盖）。
    # 每次发版重新生成时会把它的内容注入下载页（放应用截图等）。
    _preview_path = "docs/download-preview.md"
    if os.path.isfile(_preview_path):
        _preview = open(_preview_path, encoding="utf-8").read().strip()
        if _preview:
            lines.append(_preview)
            lines.append("")

    if assets:
        rows = []
        for a in assets:
            os_name, arch = platform_of(a["name"])
            size = a.get("size", 0)
            size_s = f"{size/1048576:.1f} MB" if size >= 1048576 else f"{size/1024:.0f} KB"
            rows.append((os_name, arch, a["name"], size_s, a["browser_download_url"]))
        rows.sort(key=lambda r: (r[0], r[1], r[2]))

        lines.append("| 平台 | 文件 | 大小 | 下载 |")
        lines.append("|---|---|---|---|")
        for os_name, arch, fn, size_s, url in rows:
            plat = os_name + (f" · {arch}" if arch else "")
            lines.append(f"| {plat} | `{fn}` | {size_s} | [⬇️ 下载]({url}) |")
        lines.append("")
    else:
        lines.append("> 本仓库没有附带二进制资产；安装方式见文档。")
        lines.append("")

    os.makedirs("docs", exist_ok=True)
    with open("docs/download.md", "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    print(f"docs/download.md <- {project} {tag} ({len(assets)} assets)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
