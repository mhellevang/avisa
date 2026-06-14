"""Konfig som kan endres i drift. Verdier lagres i Setting-tabellen og
overstyrer env-defaultene fra config.settings. Lest på nytt ved behov — billig
nok på dette trafikknivået."""

from .config import settings
from .db import get_session
from .models import Setting

# Default-verdiene kommer fra env / config.Settings.
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
    """Avisas målspråk (ISO-kode)."""
    return (get("paper_lang") or "no").strip().lower()


def skip_langs() -> set[str]:
    """Kildespråk brukeren eksplisitt vil la stå urørt (utenom målspråket)."""
    raw = get("translate_skip_langs")
    return {p.strip().lower() for p in raw.split(",") if p.strip()}


def should_translate(source_lang: str) -> bool:
    """True hvis en sak fra en kilde med dette språket skal oversettes til
    målspråket. Oversett aldri det som alt er på målspråket eller står i
    brukerens «la stå urørt»-liste."""
    sl = (source_lang or "").strip().lower()
    if not sl:
        return True  # ukjent språk → oversett heller enn å vise fremmedspråk
    return sl != paper_lang() and sl not in skip_langs()
