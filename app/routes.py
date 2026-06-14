import json
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import select

from . import auth, llm, progress, runtime_config, scheduler
from .config import settings
from .db import get_session
from .fetchers import discover
from .models import Article, Edition, EditionItem, Source, utcnow
from .pipeline import run_pipeline

router = APIRouter()

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

_NO_MONTHS = [
    "januar", "februar", "mars", "april", "mai", "juni",
    "juli", "august", "september", "oktober", "november", "desember",
]
_NO_DAYS = ["mandag", "tirsdag", "onsdag", "torsdag", "fredag", "lørdag", "søndag"]


def no_date(dt: datetime | None) -> str:
    if not dt:
        return ""
    return f"{_NO_DAYS[dt.weekday()]} {dt.day}. {_NO_MONTHS[dt.month - 1]} {dt.year}"


def no_datetime(dt: datetime | None) -> str:
    if not dt:
        return ""
    return f"{dt.day}. {_NO_MONTHS[dt.month - 1]} kl. {dt.strftime('%H:%M')}"


def domain(url: str) -> str:
    try:
        from urllib.parse import urlparse

        return urlparse(url).netloc.replace("www.", "")
    except Exception:
        return ""


templates.env.globals["no_date"] = no_date
templates.env.globals["no_datetime"] = no_datetime
templates.env.globals["domain"] = domain
# Callable så tittelen kan endres i drift (innstillinger/veiviser).
templates.env.globals["paper_title"] = runtime_config.paper_title


def _latest_edition_items(s):
    ed = s.exec(select(Edition).order_by(Edition.id.desc())).first()
    items: list[tuple[EditionItem, Article]] = []
    if ed:
        rows = s.exec(
            select(EditionItem, Article)
            .join(Article, EditionItem.article_id == Article.id)
            .where(EditionItem.edition_id == ed.id)
            .order_by(EditionItem.rank)
        ).all()
        items = [(ei, a) for ei, a in rows]
    return ed, items


def _source_names(s) -> dict[int, str]:
    return {src.id: src.name for src in s.exec(select(Source)).all()}


@router.get("/", response_class=HTMLResponse)
def front(request: Request):
    with get_session() as s:
        ed, items = _latest_edition_items(s)
        source_names = _source_names(s)

    lead = next((a for ei, a in items if ei.slot == "lead"), None)
    secondary = [a for ei, a in items if ei.slot == "secondary"]
    body = [a for ei, a in items if ei.slot == "body"]

    # Grupper body-saker etter seksjon for et avis-aktig oppsett.
    sections: dict[str, list[Article]] = {}
    for a in body:
        sections.setdefault(a.section, []).append(a)

    return templates.TemplateResponse(
        "edition.html",
        {
            "request": request,
            "edition": ed,
            "lead": lead,
            "secondary": secondary,
            "sections": sections,
            "source_names": source_names,
            "llm_enabled": llm.enabled(),
            "provider_label": llm.provider_label(),
            "auth_enabled": auth.auth_enabled(),
            "is_admin": auth.is_authed(request),
        },
    )


@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request, next: str = "/settings", error: int = 0):
    if not auth.auth_enabled() or auth.is_authed(request):
        return RedirectResponse(url=next or "/settings", status_code=303)
    return templates.TemplateResponse(
        "login.html", {"request": request, "next": next, "error": bool(error)}
    )


@router.post("/login")
def login_submit(password: str = Form(...), next: str = Form("/settings")):
    if auth.check_password(password):
        resp = RedirectResponse(url=next or "/settings", status_code=303)
        resp.set_cookie(
            auth.COOKIE_NAME,
            auth.make_token(),
            httponly=True,
            samesite="lax",
            max_age=60 * 60 * 24 * 30,
        )
        return resp
    return RedirectResponse(
        url=f"/login?error=1&next={quote(next or '/settings')}", status_code=303
    )


@router.post("/logout")
def logout():
    resp = RedirectResponse(url="/", status_code=303)
    resp.delete_cookie(auth.COOKIE_NAME)
    return resp


@router.get("/article/{article_id}", response_class=HTMLResponse)
def article(request: Request, article_id: int):
    with get_session() as s:
        a = s.get(Article, article_id)
        if not a:
            return HTMLResponse("Fant ikke artikkelen", status_code=404)

        # Oversett ved åpning hvis ikke alt gjort (saker utenfor forsiden er ikke
        # foroversatt). Caches, så bare første åpning koster.
        if a.translated_at is None and llm.enabled():
            res = llm.translate_to_norwegian(a.title, a.summary or "", a.content or "")
            if res:
                a.title_no = res.get("title", a.title)
                a.summary_no = res.get("summary", a.summary)
                if a.content:
                    a.content_no = res.get("content", a.content)
            else:
                a.title_no, a.summary_no, a.content_no = a.title, a.summary, (a.content or None)
            a.translated_at = utcnow()
            s.commit()
            s.refresh(a)

        # Forrige/neste innenfor nyeste utgave, så man kan bla som i en avis.
        ed = s.exec(select(Edition).order_by(Edition.id.desc())).first()
        prev_item = next_item = None
        if ed:
            rows = s.exec(
                select(EditionItem, Article)
                .join(Article, EditionItem.article_id == Article.id)
                .where(EditionItem.edition_id == ed.id)
                .order_by(EditionItem.rank)
            ).all()
            order = [art for _ei, art in rows]
            ids = [art.id for art in order]
            if article_id in ids:
                i = ids.index(article_id)
                if i > 0:
                    p = order[i - 1]
                    prev_item = {"id": p.id, "title": p.display_title}
                if i < len(order) - 1:
                    n = order[i + 1]
                    next_item = {"id": n.id, "title": n.display_title}

        # Lesetid (~200 ord/min).
        text = a.content_no or a.content or a.display_summary or ""
        words = len(text.split())
        read_min = max(1, round(words / 200)) if words else None

    return templates.TemplateResponse(
        "article.html",
        {
            "request": request,
            "a": a,
            "prev_item": prev_item,
            "next_item": next_item,
            "read_min": read_min,
        },
    )


@router.get("/more", response_class=HTMLResponse)
def more(request: Request, offset: int = 0, limit: int = 30):
    """Flere saker — paginerer over ferskt korpus utenfor nyeste utgave.
    Umiddelbart, ingen prosessering on-the-fly."""
    with get_session() as s:
        ed = s.exec(select(Edition).order_by(Edition.id.desc())).first()
        in_edition: set[int] = set()
        if ed:
            in_edition = {
                ei.article_id
                for ei in s.exec(
                    select(EditionItem).where(EditionItem.edition_id == ed.id)
                ).all()
            }
        q = select(Article).order_by(Article.fetched_at.desc())
        all_recent = s.exec(q).all()
        pool = [
            a for a in all_recent
            if a.id not in in_edition
            and not (settings.filter_paywalled and a.paywalled)
        ]
        page = pool[offset : offset + limit]
        has_next = offset + limit < len(pool)
        source_names = _source_names(s)

    return templates.TemplateResponse(
        "more.html",
        {
            "request": request,
            "articles": page,
            "source_names": source_names,
            "offset": offset,
            "limit": limit,
            "next_offset": offset + limit,
            "has_next": has_next,
        },
    )


@router.post("/refresh")
def refresh(background_tasks: BackgroundTasks):
    """Trigger pipeline nå (i bakgrunnen), og send brukeren tilbake til
    forsiden. Dette er 'hent nytt innhold live'-knappen."""
    background_tasks.add_task(run_pipeline)
    return RedirectResponse(url="/", status_code=303)


@router.get("/status")
def status():
    """Live pipeline-status for fremdriftsvisning på forsiden."""
    snap = progress.snapshot()
    with get_session() as s:
        ed = s.exec(select(Edition).order_by(Edition.id.desc())).first()
    snap["has_edition"] = ed is not None
    snap["edition_id"] = ed.id if ed else None
    snap["edition_built_at"] = ed.built_at.isoformat() if ed else None
    return JSONResponse(snap)


# --------------------------------------------------------------------------- #
# Tilbakemelding → justert profil
# --------------------------------------------------------------------------- #
@router.post("/feedback")
def feedback(background_tasks: BackgroundTasks, feedback: str = Form(...)):
    feedback = feedback.strip()
    if feedback:
        current = runtime_config.preferences()
        revised = llm.revise_preferences(current, feedback)
        if revised:
            runtime_config.set_value("preferences", revised)
        else:
            # Uten LLM: legg tilbakemeldingen som et notat i profilen.
            runtime_config.set_value("preferences", f"{current}\n- {feedback}")
        # Bygg avisa på nytt med justert profil.
        background_tasks.add_task(run_pipeline)
    return RedirectResponse(url="/", status_code=303)


# --------------------------------------------------------------------------- #
# Innstillinger
# --------------------------------------------------------------------------- #
@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, saved: int = 0, msg: str = ""):
    with get_session() as s:
        sources = s.exec(select(Source).order_by(Source.id)).all()
    return templates.TemplateResponse(
        "settings.html",
        {
            "request": request,
            "sources": sources,
            "paper_title_val": runtime_config.paper_title(),
            "preferences_val": runtime_config.preferences(),
            "front_page_size_val": runtime_config.front_page_size(),
            "poll_minutes_val": runtime_config.poll_minutes(),
            "provider_label": llm.provider_label(),
            "llm_enabled": llm.enabled(),
            "saved": bool(saved),
            "msg": msg,
            "chat": _load_chat(),
            "auth_enabled": auth.auth_enabled(),
        },
    )


@router.post("/settings")
def settings_save(
    paper_title: str = Form(...),
    preferences: str = Form(...),
    front_page_size: int = Form(...),
    poll_minutes: int = Form(...),
):
    runtime_config.set_value("paper_title", paper_title.strip())
    runtime_config.set_value("preferences", preferences.strip())
    runtime_config.set_value("front_page_size", str(max(1, front_page_size)))
    poll = max(1, poll_minutes)
    runtime_config.set_value("poll_minutes", str(poll))
    scheduler.reschedule(poll)
    return RedirectResponse(url="/settings?saved=1", status_code=303)


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


@router.post("/configure")
def configure(background_tasks: BackgroundTasks, command: str = Form(...)):
    """Snakk med konfiguratoren: fritekst → svar + handlinger via LLM."""
    command = command.strip()
    if not command:
        return RedirectResponse(url="/settings#chat", status_code=303)

    history = _load_chat()
    if not llm.enabled():
        history.append({"role": "user", "text": command})
        history.append({"role": "bot", "text": "Jeg trenger en LLM for å forstå fritekst. Sett OPENROUTER_API_KEY, eller kjør lokalt med en innlogget claude-session."})
        _save_chat(history)
        return RedirectResponse(url="/settings#chat", status_code=303)

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
    )
    history.append({"role": "user", "text": command})
    if result is None:
        history.append({"role": "bot", "text": "Beklager, jeg klarte ikke å tolke den. Prøv å være litt mer konkret?"})
        _save_chat(history)
        return RedirectResponse(url="/settings#chat", status_code=303)

    actions = result.get("actions", [])
    reply = (result.get("reply") or "").strip()
    done: list[str] = []
    rebuild = False
    for act in actions:
        if not isinstance(act, dict):
            continue
        kind = act.get("action")
        if kind == "add_source":
            prop = discover.propose(act.get("query", ""))
            if prop.get("ok"):
                with get_session() as s:
                    s.add(Source(
                        name=prop["name"], kind=prop["kind"], url=prop["url"],
                        section=prop["section"], enabled=True, config=prop.get("config"),
                    ))
                    s.commit()
                done.append(f"la til «{prop['name']}»")
                rebuild = True
            else:
                done.append(f"fant ikke kilde for «{act.get('query', '')}»")
        elif kind in ("remove_source", "enable_source", "disable_source"):
            name = (act.get("name") or "").strip().lower()
            with get_session() as s:
                match = next(
                    (x for x in s.exec(select(Source)).all() if x.name.lower() == name), None
                )
                if not match:
                    done.append(f"fant ikke kilden «{act.get('name', '')}»")
                elif kind == "remove_source":
                    s.delete(match); s.commit()
                    done.append(f"fjernet «{match.name}»"); rebuild = True
                else:
                    match.enabled = (kind == "enable_source"); s.commit()
                    done.append(f"{'skrudde på' if match.enabled else 'skrudde av'} «{match.name}»")
                    rebuild = True
        elif kind == "set_preferences" and act.get("value"):
            runtime_config.set_value("preferences", str(act["value"]).strip())
            done.append("oppdaterte profilen"); rebuild = True
        elif kind == "set_title" and act.get("value"):
            runtime_config.set_value("paper_title", str(act["value"]).strip())
            done.append(f"satte tittel til «{act['value']}»")
        elif kind == "set_front_page_size" and act.get("value"):
            try:
                runtime_config.set_value("front_page_size", str(max(1, int(act["value"]))))
                done.append(f"satte forsidestørrelse til {int(act['value'])}"); rebuild = True
            except (TypeError, ValueError):
                pass
        elif kind == "set_poll_minutes" and act.get("value"):
            try:
                poll = max(1, int(act["value"]))
                runtime_config.set_value("poll_minutes", str(poll))
                scheduler.reschedule(poll)
                done.append(f"satte poll-intervall til {poll} min")
            except (TypeError, ValueError):
                pass

    # Bygg konfiguratorens svar: LLM-svaret + faktisk kvittering.
    bot = reply or ("Gjort." if done else "Forsto meldingen, men fant ingenting å endre.")
    if done:
        bot += "\n\n✓ " + "; ".join(done) + "."
    if rebuild:
        background_tasks.add_task(run_pipeline)
        bot += "\nBygger avisa på nytt …"
    history.append({"role": "bot", "text": bot})
    _save_chat(history)
    return RedirectResponse(url="/settings#chat", status_code=303)


@router.post("/sources/discover")
def source_discover(query: str = Form(...)):
    """Smart kilde-oppsett: finn ut av en bar URL/domene og legg den til."""
    prop = discover.propose(query)
    if prop.get("ok"):
        with get_session() as s:
            s.add(
                Source(
                    name=prop["name"],
                    kind=prop["kind"],
                    url=prop["url"],
                    section=prop["section"],
                    enabled=True,
                    config=prop.get("config"),
                )
            )
            s.commit()
        msg = (
            f"✓ La til «{prop['name']}» — {prop['kind'].upper()} i {prop['section']}, "
            f"{prop['entries']} saker funnet."
        )
    else:
        msg = "⚠ " + prop.get("reason", "Klarte ikke å finne ut av kilden.")
    return RedirectResponse(url=f"/settings?msg={quote(msg)}", status_code=303)


@router.post("/sources/add")
def source_add(
    name: str = Form(...),
    kind: str = Form(...),
    url: str = Form(...),
    section: str = Form("Nyheter"),
    config: str = Form(""),
):
    cfg = None
    config = config.strip()
    if config:
        try:
            json.loads(config)  # valider
            cfg = config
        except json.JSONDecodeError:
            cfg = None  # ignorer ugyldig JSON heller enn å feile
    with get_session() as s:
        s.add(
            Source(
                name=name.strip(),
                kind=kind.strip(),
                url=url.strip(),
                section=(section.strip() or "Nyheter"),
                enabled=True,
                config=cfg,
            )
        )
        s.commit()
    return RedirectResponse(url="/settings", status_code=303)


@router.post("/sources/{source_id}/toggle")
def source_toggle(source_id: int):
    with get_session() as s:
        src = s.get(Source, source_id)
        if src:
            src.enabled = not src.enabled
            s.commit()
    return RedirectResponse(url="/settings", status_code=303)


@router.post("/sources/{source_id}/delete")
def source_delete(source_id: int):
    with get_session() as s:
        src = s.get(Source, source_id)
        if src:
            s.delete(src)
            s.commit()
    return RedirectResponse(url="/settings", status_code=303)
