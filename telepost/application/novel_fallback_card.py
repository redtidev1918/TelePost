"""Novel fallback card (§novel-cover).

Pixiv novels without a real cover still need a channel root post: the spec is
``fallback root + TXT reply``, never a TXT-only orphan and never ``delivery
returned no messages``. TelePost renders a lightweight PNG card with Pillow (a
dependency the media pipeline already carries) — no new runtime dependency, no
memory blow-up: one fixed-size RGB canvas, fonts cached process-wide.

This is presentation, not business state: rendering failure returns ``None``
and the publication falls back to a text-only root. It must never raise into
the delivery path.
"""
from __future__ import annotations

import logging
import os
import tempfile
from functools import lru_cache
from typing import List, Optional

logger = logging.getLogger(__name__)

#: 16:9 card that Telegram serves as a normal photo; RGB keeps PNG output small.
CARD_WIDTH = 1200
CARD_HEIGHT = 675
#: Bounded PNG budget: a card is presentation filler, never a heavy asset.
MAX_CARD_BYTES = 2 * 1024 * 1024

_BG_COLOR = (24, 26, 32)
_ACCENT_COLOR = (92, 158, 255)
_TEXT_COLOR = (235, 238, 245)
_MUTED_COLOR = (140, 148, 160)

#: CJK-capable fonts first — novel titles are almost always CJK. Each candidate
#: is optional: the renderer degrades to whatever the host actually has, and
#: PIL's built-in font as the last resort. The production image installs
#: fonts-wqy-microhei (≈7 MB); no heavyweight noto-cjk.
_FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/System/Library/Fonts/PingFang.ttc",
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/arial.ttf",
)

_FONT_SIZES = {"title": 56, "meta": 30, "small": 24}


@lru_cache(maxsize=None)
def _load_font(size: int):
    from PIL import ImageFont
    for path in _FONT_CANDIDATES:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size=size)
            except OSError:
                continue
    try:
        # Pillow >= 10.1 can build a scalable font from the bundled glyphs.
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _wrap(text: str, limit: int, max_lines: int = 3) -> List[str]:
    """Greedy wrap that never cuts a single long line beyond max_lines."""
    lines: List[str] = []
    for raw_line in str(text).splitlines() or [""]:
        current = ""
        for ch in raw_line.strip():
            if len(current) + 1 > limit:
                lines.append(current)
                current = ch
                if len(lines) >= max_lines:
                    break
            else:
                current += ch
        else:
            if current:
                lines.append(current)
        if len(lines) >= max_lines:
            break
    if not lines:
        lines = ["无标题"]
    return lines[:max_lines]


def render_novel_fallback_card(title: str, tags=None, *, author: str = "") -> Optional[str]:
    """Render the fallback card PNG and return its temp path, or ``None``.

    ``None`` means "presentation enrichment unavailable" — callers must treat
    it as text-only root, never as a publication failure.
    """
    try:
        from PIL import Image, ImageDraw
    except Exception:
        logger.warning("novel fallback card skipped: Pillow unavailable")
        return None

    path = None
    try:
        image = Image.new("RGB", (CARD_WIDTH, CARD_HEIGHT), _BG_COLOR)
        draw = ImageDraw.Draw(image)
        # Accent band keeps the card recognizable without gradients or assets.
        draw.rectangle([0, 0, CARD_WIDTH, 12], fill=_ACCENT_COLOR)

        safe_title = " ".join(str(title or "").split()) or "无标题"
        lines = _wrap(safe_title, 16)
        y = 220
        for line in lines:
            draw.text((80, y), line, font=_load_font(_FONT_SIZES["title"]),
                      fill=_TEXT_COLOR)
            y += 78

        tag_text = " ".join(f"#{t}" for t in (tags or [])[:5])
        if tag_text:
            draw.text((80, 480), tag_text, font=_load_font(_FONT_SIZES["meta"]),
                      fill=_MUTED_COLOR)
        if author:
            draw.text((80, 530), str(author)[:40], font=_load_font(_FONT_SIZES["small"]),
                      fill=_MUTED_COLOR)
        draw.text((80, 596), "📖 小说 · TelePost", font=_load_font(_FONT_SIZES["small"]),
                  fill=_ACCENT_COLOR)

        fd, path = tempfile.mkstemp(prefix="novel-card-", suffix=".png")
        with os.fdopen(fd, "wb") as handle:
            image.save(handle, format="PNG", optimize=True)
        try:
            if os.path.getsize(path) > MAX_CARD_BYTES:
                logger.warning("novel fallback card exceeded %s bytes; skipping",
                               MAX_CARD_BYTES)
                os.unlink(path)
                return None
        except OSError:
            pass
        return path
    except Exception:
        logger.warning("novel fallback card rendering failed", exc_info=True)
        if path and os.path.exists(path):
            try:
                os.unlink(path)
            except OSError:
                pass
        return None
