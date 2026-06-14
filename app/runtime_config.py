"""Configuration that can be changed at runtime. Values are stored in the
Setting table and override the env defaults from config.settings. Re-read on
demand — cheap enough at this traffic level."""

from .config import settings
from .db import get_session
from .models import Setting

# The default values come from env / config.Settings.
DEFAULTS: dict[str, str] = {
    "paper_title": settings.paper_title,
    "preferences": settings.preferences,
    "front_page_size": str(settings.front_page_size),
    "poll_minutes": str(settings.poll_minutes),
    "translate_skip_langs": settings.translate_skip_langs,
    "paper_lang": settings.paper_lang,
}


def get(key: str) -> str:
    with get_session() as s:
        row = s.get(Setting, key)
        if row is not None:
            return row.value
    return DEFAULTS.get(key, "")


def set_value(key: str, value: str) -> None:
    with get_session() as s:
        row = s.get(Setting, key)
        if row:
            row.value = value
        else:
            s.add(Setting(key=key, value=value))
        s.commit()


def _as_int(key: str, fallback: int) -> int:
    try:
        return int(get(key))
    except (TypeError, ValueError):
        return fallback


def paper_title() -> str:
    return get("paper_title")


def preferences() -> str:
    return get("preferences")


def front_page_size() -> int:
    return _as_int("front_page_size", settings.front_page_size)


def poll_minutes() -> int:
    return _as_int("poll_minutes", settings.poll_minutes)


def paper_lang() -> str:
    """The paper's target language (ISO code)."""
    return (get("paper_lang") or "no").strip().lower()


def skip_langs() -> set[str]:
    """Source languages the user explicitly wants left untouched (besides the
    target language)."""
    raw = get("translate_skip_langs")
    return {p.strip().lower() for p in raw.split(",") if p.strip()}


def should_translate(source_lang: str) -> bool:
    """True if an article from a source in this language should be translated
    into the target language. Never translate what is already in the target
    language or appears in the user's "leave untouched" list."""
    sl = (source_lang or "").strip().lower()
    if not sl:
        return True  # unknown language → translate rather than show a foreign language
    return sl != paper_lang() and sl not in skip_langs()
