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
