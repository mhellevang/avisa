"""Configuration that can be changed at runtime. Values are stored in the
Setting table and override the env defaults from config.settings. Re-read on
demand — cheap enough at this traffic level."""

import json
import re
from typing import Optional

from .catalog import TOPICS
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


def topic_keys() -> list[str]:
    """Editorial topics chosen in onboarding (the profile builder)."""
    raw = get("profile_topics")
    return [p.strip() for p in raw.split(",") if p.strip()]


def custom_topics() -> list[dict]:
    """Editorial topics added at runtime (by the user or the LLM configurator),
    stored as JSON in Setting. Each: {"key", "label", "phrase"}. Built-in topics
    live in catalog.TOPICS; these extend them."""
    raw = get("custom_topics")
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return []
    out: list[dict] = []
    if isinstance(data, list):
        for t in data:
            if isinstance(t, dict) and t.get("key") and t.get("phrase"):
                out.append({
                    "key": str(t["key"]),
                    "label": str(t.get("label") or t["key"]),
                    "phrase": str(t["phrase"]),
                })
    return out


def all_topics() -> list[dict]:
    """Built-in topics plus custom ones, normalized to label_en/label_no so the
    settings page renders them uniformly. Custom labels are language-neutral
    (shown as-is in both UI languages)."""
    topics = list(TOPICS)
    keys = {t["key"] for t in topics}
    for t in custom_topics():
        if t["key"] in keys:
            continue
        keys.add(t["key"])
        topics.append({
            "key": t["key"],
            "label_en": t["label"],
            "label_no": t["label"],
            "phrase": t["phrase"],
        })
    return topics


def add_topic(key: str, label: str, phrase: str) -> Optional[str]:
    """Add a custom editorial topic. Returns the normalized key it was stored
    under, or None if rejected (no phrase, or the key shadows a built-in or an
    existing custom topic). Does not select it — that's the caller's job."""
    key = re.sub(r"[^a-z0-9]+", "-", (key or "").strip().lower()).strip("-")
    label = (label or "").strip()
    phrase = (phrase or "").strip()
    if not key or not phrase:
        return None
    if any(t["key"] == key for t in TOPICS):
        return None  # don't shadow a built-in topic
    existing = custom_topics()
    if any(t["key"] == key for t in existing):
        return None
    existing.append({"key": key, "label": label or key, "phrase": phrase})
    set_value("custom_topics", json.dumps(existing, ensure_ascii=False))
    return key


def remove_topic(key: str) -> bool:
    """Drop a custom topic from storage. Returns True if one was removed.
    Built-in topics can't be deleted (only unchecked); unchecking is the
    caller's job via profile_topics."""
    key = (key or "").strip().lower()
    existing = custom_topics()
    kept = [t for t in existing if t["key"] != key]
    if len(kept) == len(existing):
        return False
    set_value("custom_topics", json.dumps(kept, ensure_ascii=False))
    return True


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
