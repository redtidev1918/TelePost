"""Bot presentation language; never translate submitted content or wire values."""
from functools import lru_cache
import gettext
from pathlib import Path


def normalize_language(value: str) -> str:
    language = (value or "zh").strip().lower().replace("_", "-")
    aliases = {"zh-cn": "zh", "zh-hans": "zh", "en-us": "en", "en-gb": "en"}
    language = aliases.get(language, language)
    if language not in ("zh", "en"):
        raise ValueError("BOT_LANGUAGE must be zh or en")
    return language


@lru_cache(maxsize=2)
def _catalog(language: str):
    if language == "zh":
        return gettext.NullTranslations()
    return gettext.translation(
        "telepost", localedir=Path(__file__).with_name("locales"),
        languages=[language],
    )


def tr(message: str) -> str:
    # Lazy import avoids settings -> language validation -> settings cycles.
    from config import settings
    return _catalog(settings.BOT_LANGUAGE).gettext(message)
