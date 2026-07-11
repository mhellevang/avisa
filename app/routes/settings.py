"""Settings page and the free-text configurator chat."""

import json

from fastapi import APIRouter, BackgroundTasks, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import select

from .. import auth, catalog, i18n, llm, runtime_config, scheduler
from ..config import settings
from ..db import get_session
from ..fetchers import discover
from ..models import Article, Source
from ..pipeline import run_pipeline
from .common import templates, ui_lang
from .sources import add_source

router = APIRouter()


def _label(item: dict) -> str:
    return item["label_no"] if ui_lang() == "no" else item["label_en"]


def _translate_summary(sources, plang: str, skip: set[str]) -> dict:
    """What translation will actually do, derived from the sources' languages,
    the paper language and the skip list. Lets the UI explain it plainly."""
    translated, kept = set(), set()
    for src in sources:
        if not src.enabled:
            continue
        sl = (src.lang or "").strip().lower()
        if not sl:
            continue  # unknown language — translated if foreign, but unlabelable here
        if sl == plang or sl in skip:
            kept.add(sl)
        else:
            translated.add(sl)
    return {
        "translated": sorted((c, i18n.lang_label(c)) for c in translated),
        "kept": sorted((c, i18n.lang_label(c)) for c in kept),
    }


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, saved: int = 0, msg: str = "", region: str = ""):
    with get_session() as s:
        sources = s.exec(select(Source).order_by(Source.id)).all()
    plang = runtime_config.paper_lang()
    skip = runtime_config.skip_langs()
    # Checkbox candidates: languages present among the sources (≠ target language),
    # plus any skip languages that no longer have a source.
    cand = {(src.lang or "").strip().lower() for src in sources}
    cand |= skip
    cand.discard("")
    cand.discard(plang)
    skip_options = sorted((c, i18n.lang_label(c)) for c in cand)

    selected_region = (region or runtime_config.get("home_region") or "no").strip().lower()
    have_urls = {(src.url or "").strip() for src in sources}
    proposed = [
        {**c, "label": c["name"], "already": c["url"] in have_urls}
        for c in catalog.suggested_sources(selected_region)
    ]

    selected_topics = set(runtime_config.topic_keys())
    topics = [
        {"key": t["key"], "label": _label(t), "checked": t["key"] in selected_topics}
        for t in catalog.TOPICS
    ]
    # If the user has a custom free-text profile from before (different from the
    # default) and hasn't picked topics yet, seed the refinement box with it so
    # switching to topics doesn't silently drop it.
    extra_val = runtime_config.get("preferences_extra")
    if not selected_topics and not extra_val:
        current = runtime_config.preferences()
        if current and current != settings.preferences:
            extra_val = current

    return templates.TemplateResponse(
        "settings.html",
        {
            "request": request,
            "sources": sources,
            "paper_title_val": runtime_config.paper_title(),
            "preferences_val": runtime_config.preferences(),
            "preferences_extra_val": extra_val,
            "topics": topics,
            "front_page_size_val": runtime_config.front_page_size(),
            "poll_minutes_val": runtime_config.poll_minutes(),
            "paper_lang_val": plang,
            "ui_lang_options": [(c, i18n.lang_label(c)) for c in i18n.UI_LANGS],
            "source_lang_options": [(c, i18n.lang_label(c)) for c in i18n.LANG_NAMES],
            "skip_options": skip_options,
            "skip_langs_set": skip,
            "translate_summary": _translate_summary(sources, plang, skip),
            "paper_lang_label": i18n.lang_label(plang),
            "region_options": [(r["code"], _label(r)) for r in catalog.REGIONS],
            "selected_region": selected_region,
            "proposed_sources": proposed,
            "provider_label": llm.provider_label(),
            "llm_enabled": llm.enabled(),
            "llm_health": llm.health(),
            "saved": bool(saved),
            "msg": msg,
            "chat": _load_chat(),
            "auth_enabled": auth.auth_enabled(),
        },
    )


@router.post("/settings")
def settings_save(
    background_tasks: BackgroundTasks,
    paper_title: str = Form(...),
    front_page_size: int = Form(...),
    poll_minutes: int = Form(...),
    topics: list[str] = Form(default=[]),
    preferences_extra: str = Form(""),
    paper_lang: str = Form("en"),
    skip_langs: list[str] = Form(default=[]),
):
    runtime_config.set_value("paper_title", paper_title.strip())
    # Profile is derived from the chosen topics plus optional free-text
    # refinement — rebuild_preferences is the single writer of `preferences`.
    valid = {t["key"] for t in catalog.TOPICS}
    chosen = [t for t in topics if t in valid]
    runtime_config.set_value("profile_topics", ",".join(chosen))
    runtime_config.set_value("preferences_extra", preferences_extra.strip())
    runtime_config.rebuild_preferences()
    runtime_config.set_value("front_page_size", str(max(1, front_page_size)))
    poll = max(1, poll_minutes)
    runtime_config.set_value("poll_minutes", str(poll))
    # Skip languages come in as checked boxes; normalize to comma-separated.
    langs = ",".join(sorted({p.strip().lower() for p in skip_langs if p.strip()}))
    runtime_config.set_value("translate_skip_langs", langs)

    # Switching target language: the old translation cache is in the wrong
    # language. Reset it and rebuild the paper so everything is re-translated
    # to the new language.
    new_lang = (paper_lang or "en").strip().lower()
    lang_changed = new_lang != runtime_config.paper_lang()
    runtime_config.set_value("paper_lang", new_lang)
    if lang_changed:
        with get_session() as s:
            stale = s.exec(
                select(Article).where(
                    Article.title_no != None,  # noqa: E711
                    (Article.translated_lang == None) | (Article.translated_lang != new_lang),  # noqa: E711
                )
            ).all()
            for a in stale:
                a.title_no = a.summary_no = a.content_no = None
                a.translated_lang = None
                a.translated_at = None
            s.commit()
        background_tasks.add_task(run_pipeline)

    scheduler.reschedule(poll)
    return RedirectResponse(url="/settings?saved=1", status_code=303)


# --------------------------------------------------------------------------- #
# Configurator chat
# --------------------------------------------------------------------------- #
def _load_chat() -> list:
    raw = runtime_config.get("config_chat")
    try:
        return json.loads(raw) if raw else []
    except (ValueError, TypeError):
        return []


def _save_chat(history: list) -> None:
    runtime_config.set_value("config_chat", json.dumps(history[-12:]))


@router.post("/configure/clear")
def configure_clear():
    runtime_config.set_value("config_chat", "[]")
    return RedirectResponse(url="/settings#chat", status_code=303)


# Each handler takes the action dict and returns (receipt_line | None, rebuild).
def _act_add_source(act) -> tuple[str | None, bool]:
    prop = discover.propose(act.get("query", ""))
    if prop.get("ok"):
        add_source(prop)
        return i18n.current("added «{name}»", name=prop["name"]), True
    return i18n.current("couldn't find a source for «{query}»", query=act.get("query", "")), False


def _act_source_by_name(act) -> tuple[str | None, bool]:
    kind = act.get("action")
    name = (act.get("name") or "").strip().lower()
    with get_session() as s:
        match = next(
            (x for x in s.exec(select(Source)).all() if x.name.lower() == name), None
        )
        if not match:
            return i18n.current("couldn't find the source «{name}»", name=act.get("name", "")), False
        if kind == "remove_source":
            s.delete(match)
            s.commit()
            return i18n.current("removed «{name}»", name=match.name), True
        match.enabled = (kind == "enable_source")
        s.commit()
        key = "turned on «{name}»" if match.enabled else "turned off «{name}»"
        return i18n.current(key, name=match.name), True


def _act_set_refinement(act) -> tuple[str | None, bool]:
    runtime_config.set_value("preferences_extra", str(act.get("value") or "").strip())
    runtime_config.rebuild_preferences()
    return i18n.current("updated the profile"), True


def _act_set_title(act) -> tuple[str | None, bool]:
    if not act.get("value"):
        return None, False
    runtime_config.set_value("paper_title", str(act["value"]).strip())
    return i18n.current("set the title to «{value}»", value=act["value"]), False


def _act_set_front_page_size(act) -> tuple[str | None, bool]:
    try:
        value = int(act["value"])
    except (KeyError, TypeError, ValueError):
        return None, False
    runtime_config.set_value("front_page_size", str(max(1, value)))
    return i18n.current("set the front-page size to {value}", value=value), True


def _act_set_poll_minutes(act) -> tuple[str | None, bool]:
    try:
        poll = max(1, int(act["value"]))
    except (KeyError, TypeError, ValueError):
        return None, False
    runtime_config.set_value("poll_minutes", str(poll))
    scheduler.reschedule(poll)
    return i18n.current("set the poll interval to {value} min", value=poll), False


def _act_topic(act) -> tuple[str | None, bool]:
    kind = act.get("action")
    key = str(act.get("key") or "").strip().lower()
    by_key = {t["key"]: t for t in catalog.TOPICS}
    if key not in by_key:
        return None, False
    chosen = runtime_config.topic_keys()
    if kind == "select_topic" and key not in chosen:
        chosen.append(key)
    elif kind == "deselect_topic" and key in chosen:
        chosen = [k for k in chosen if k != key]
    else:
        return None, False
    runtime_config.set_value("profile_topics", ",".join(chosen))
    runtime_config.rebuild_preferences()
    if kind == "select_topic":
        return i18n.current("added the topic «{label}»", label=_label(by_key[key])), True
    return i18n.current("removed the topic «{key}»", key=_label(by_key[key])), True


_ACTION_HANDLERS = {
    "add_source": _act_add_source,
    "remove_source": _act_source_by_name,
    "enable_source": _act_source_by_name,
    "disable_source": _act_source_by_name,
    "set_refinement": _act_set_refinement,
    "set_title": _act_set_title,
    "set_front_page_size": _act_set_front_page_size,
    "set_poll_minutes": _act_set_poll_minutes,
    "select_topic": _act_topic,
    "deselect_topic": _act_topic,
}


@router.post("/configure")
def configure(background_tasks: BackgroundTasks, command: str = Form(...)):
    """Talk to the configurator: free text → reply + actions via the LLM."""
    command = command.strip()
    if not command:
        return RedirectResponse(url="/settings#chat", status_code=303)

    history = _load_chat()
    if not llm.enabled():
        history.append({"role": "user", "text": command})
        history.append({"role": "bot", "text": i18n.current("I need an LLM to understand free text. Set OPENROUTER_API_KEY, or run locally with a logged-in claude session.")})
        _save_chat(history)
        return RedirectResponse(url="/settings#chat", status_code=303)

    plang = runtime_config.paper_lang()
    with get_session() as s:
        sources = s.exec(select(Source)).all()
    result = llm.interpret_config(
        command,
        sources,
        runtime_config.preferences(),
        runtime_config.paper_title(),
        runtime_config.front_page_size(),
        runtime_config.poll_minutes(),
        history=history,
        target=i18n.lang_prompt_name(plang),
        refinement=runtime_config.get("preferences_extra"),
    )
    history.append({"role": "user", "text": command})
    if result is None:
        history.append({"role": "bot", "text": i18n.current("Sorry, I couldn't interpret that. Try being a bit more specific?")})
        _save_chat(history)
        return RedirectResponse(url="/settings#chat", status_code=303)

    reply = (result.get("reply") or "").strip()
    done: list[str] = []
    rebuild = False
    for act in result.get("actions", []):
        if not isinstance(act, dict):
            continue
        handler = _ACTION_HANDLERS.get(act.get("action"))
        if not handler:
            continue
        line, needs_rebuild = handler(act)
        if line:
            done.append(line)
        rebuild = rebuild or needs_rebuild

    # Build the configurator's reply: the LLM answer + the actual receipt.
    bot = reply or (i18n.current("Done.") if done else i18n.current("Understood the message, but found nothing to change."))
    if done:
        bot += "\n\n✓ " + "; ".join(done) + "."
    if rebuild:
        background_tasks.add_task(run_pipeline)
        bot += "\n" + i18n.current("Rebuilding the paper …")
    history.append({"role": "bot", "text": bot})
    _save_chat(history)
    return RedirectResponse(url="/settings#chat", status_code=303)
