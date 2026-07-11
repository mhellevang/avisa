"""Reader-facing routes: front page, article view, "more stories", login,
pipeline refresh/status, and the feedback form."""

from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlmodel import select

from .. import auth, i18n, llm, progress, runtime_config, scheduler
from ..config import settings
from ..db import get_session
from ..markdown import body_html
from ..models import Article, Edition, EditionItem, Source, utcnow
from ..pipeline import run_pipeline
from .common import latest_edition_items, source_names, templates

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def front(request: Request):
    with get_session() as s:
        ed, items = latest_edition_items(s)
        src_names = source_names(s)

    # Pull fresh content when the edition has gone stale (e.g. the app was idle
    # past the poll interval), so opening the paper isn't stuck on old news.
    scheduler.refresh_if_stale(ed.built_at if ed else None)

    lead = next((a for ei, a in items if ei.slot == "lead"), None)
    secondary = [a for ei, a in items if ei.slot == "secondary"]
    body = [a for ei, a in items if ei.slot == "body"]
    briefs = [a for ei, a in items if ei.slot == "brief"]

    # Group body stories by section for a newspaper-like layout.
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
            "briefs": briefs,
            "source_names": src_names,
            "llm_enabled": llm.enabled(),
            "llm_health": llm.health(),
            "provider_label": llm.provider_label(),
            "auth_enabled": auth.auth_enabled(),
            "is_admin": auth.is_authed(request),
        },
    )


def _safe_next(next_url: str) -> str:
    """Only same-site relative paths — anything else makes /login?next=… an
    open redirect (phishing: log in on the real site, land on a fake one)."""
    if next_url.startswith("/") and not next_url.startswith("//"):
        return next_url
    return "/settings"


@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request, next: str = "/settings", error: int = 0):
    next = _safe_next(next)
    if not auth.auth_enabled() or auth.is_authed(request):
        return RedirectResponse(url=next, status_code=303)
    return templates.TemplateResponse(
        "login.html", {"request": request, "next": next, "error": bool(error)}
    )


@router.post("/login")
def login_submit(password: str = Form(...), next: str = Form("/settings")):
    next = _safe_next(next)
    if auth.check_password(password):
        resp = RedirectResponse(url=next, status_code=303)
        resp.set_cookie(
            auth.COOKIE_NAME,
            auth.make_token(),
            httponly=True,
            samesite="lax",
            secure=settings.cookie_secure,
            max_age=60 * 60 * 24 * 30,
        )
        return resp
    return RedirectResponse(url=f"/login?error=1&next={quote(next)}", status_code=303)


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
            return HTMLResponse(i18n.current("Article not found"), status_code=404)

        # Should the story be translated to the target language? (Not if it's
        # already in the target language or the source language is in "leave untouched".)
        src = s.get(Source, a.source_id)
        plang = runtime_config.paper_lang()
        do_translate = llm.enabled() and runtime_config.should_translate(src.lang if src else "")

        # Title + summary are translated inline if missing — short text, quick.
        # The body is fetched NON-blockingly via /article/{id}/body after the
        # page is shown, so opening never waits on a full-text translation.
        if do_translate and a.title_no is None:
            res = llm.translate_fields(
                a.title, a.summary or "", target=i18n.lang_prompt_name(plang)
            )
            # Only cache on success. Writing the ORIGINAL text into title_no on
            # a failed call would satisfy `title_no is None` forever and freeze
            # the article untranslated; leave it unset so the next open retries.
            title = (res or {}).get("title")
            if isinstance(title, str) and title.strip():
                a.title_no = title
                summary = res.get("summary")
                a.summary_no = summary if isinstance(summary, str) else a.summary
                a.translated_lang = plang
                s.commit()
                s.refresh(a)

        # The body is fetched/translated lazily via /article/{id}/body after the
        # page renders, so opening never blocks. Defer when a translation is
        # still owed OR when the body was never fetched and is empty (e.g. a
        # Hacker News link-out, or a source whose RSS ships no usable body) —
        # in the latter case /body does an on-demand full-text fetch.
        needs_fetch = not a.content and a.content_fetched_at is None
        body_translating = do_translate and bool(a.content) and a.content_no is None
        body_pending = body_translating or needs_fetch

        # Previous/next within the latest edition, so you can page through it
        # like a newspaper.
        _ed, rows = latest_edition_items(s)
        order = [art for _ei, art in rows]
        ids = [art.id for art in order]
        prev_item = next_item = None
        if article_id in ids:
            i = ids.index(article_id)
            if i > 0:
                p = order[i - 1]
                prev_item = {"id": p.id, "title": p.display_title}
            if i < len(order) - 1:
                n = order[i + 1]
                next_item = {"id": n.id, "title": n.display_title}

        # Reading time (~200 words/min).
        text = a.content_no or a.content or a.display_summary or ""
        words = len(text.split())
        read_min = max(1, round(words / 200)) if words else None

        # Source name for provenance: aggregators (Hacker News) link out to other
        # domains, so the byline domain alone (e.g. github.com) is misleading.
        source_name = src.name if src else ""

    return templates.TemplateResponse(
        "article.html",
        {
            "request": request,
            "a": a,
            "prev_item": prev_item,
            "next_item": next_item,
            "read_min": read_min,
            "body_pending": body_pending,
            "body_translating": body_translating,
            "source_name": source_name,
        },
    )


@router.post("/article/{article_id}/body")
def article_body(article_id: int):
    """Translates the body on demand and returns it as HTML paragraphs.
    Called by the article page after render, so opening isn't blocked. Cached
    (content_no + translated_at), so only the first time costs anything."""
    with get_session() as s:
        a = s.get(Article, article_id)
        if not a:
            return JSONResponse({"error": "not found"}, status_code=404)

        # On-demand full-text fetch: the body was never fetched and is empty
        # (an aggregator link-out, or a source whose RSS shipped no usable body).
        # Static only — no browser on the request path — so this can't hang the
        # worker on the Playwright stack; JS-only pages simply stay body-less.
        if not a.content and a.content_fetched_at is None:
            from ..fetchers.content import extract_static

            res = extract_static(a.url, a.title)
            if res is not None:
                if res.get("content"):
                    a.content = res["content"]
                if res.get("image") and not a.image_url:
                    a.image_url = res["image"]
                a.paywalled = bool(res.get("paywalled"))
                a.content_fetched_at = utcnow()
                s.commit()
                s.refresh(a)

        src = s.get(Source, a.source_id)
        do_translate = llm.enabled() and runtime_config.should_translate(src.lang if src else "")
        if do_translate and a.content and a.content_no is None:
            plang = runtime_config.paper_lang()
            translated = llm.translate_body(
                a.display_title, a.content, target=i18n.lang_prompt_name(plang)
            )
            # Only cache on success — caching the original as content_no would
            # freeze the article untranslated forever (see article()).
            if translated:
                a.content_no = translated
                a.translated_lang = plang
                if a.translated_at is None:
                    a.translated_at = utcnow()
                s.commit()
                s.refresh(a)
        body = a.content_no or a.content or ""
    return JSONResponse({"html": body_html(body)})


@router.get("/more", response_class=HTMLResponse)
def more(request: Request, offset: int = 0, limit: int = 30):
    """More stories — paginates over the fresh corpus outside the latest edition.
    Immediate, no on-the-fly processing. Paginated in SQL: loading the whole
    article table (with full body columns) per request gets slow as the corpus
    grows."""
    offset = max(0, offset)
    limit = max(1, min(limit, 100))
    with get_session() as s:
        ed = s.exec(select(Edition).order_by(Edition.id.desc())).first()
        in_edition: set[int] = set()
        if ed:
            in_edition = set(
                s.exec(
                    select(EditionItem.article_id).where(EditionItem.edition_id == ed.id)
                ).all()
            )
        q = select(Article).order_by(Article.fetched_at.desc())
        if in_edition:
            q = q.where(Article.id.not_in(in_edition))
        if settings.filter_paywalled:
            q = q.where(Article.paywalled == False)  # noqa: E712
        # Fetch one extra row to know whether a next page exists.
        page = s.exec(q.offset(offset).limit(limit + 1)).all()
        has_next = len(page) > limit
        page = page[:limit]
        src_names = source_names(s)

    return templates.TemplateResponse(
        "more.html",
        {
            "request": request,
            "articles": page,
            "source_names": src_names,
            "offset": offset,
            "limit": limit,
            "next_offset": offset + limit,
            "has_next": has_next,
        },
    )


@router.post("/refresh")
def refresh(background_tasks: BackgroundTasks):
    """Trigger the pipeline now (in the background) and send the user back to
    the front page. This is the 'fetch new content live' button."""
    background_tasks.add_task(run_pipeline)
    return RedirectResponse(url="/", status_code=303)


@router.get("/status")
def status():
    """Live pipeline status for the progress display on the front page."""
    snap = progress.snapshot()
    with get_session() as s:
        ed = s.exec(select(Edition).order_by(Edition.id.desc())).first()
    snap["has_edition"] = ed is not None
    snap["edition_id"] = ed.id if ed else None
    snap["edition_built_at"] = ed.built_at.isoformat() if ed else None
    snap["build_version"] = settings.build_version
    return JSONResponse(snap)


# --------------------------------------------------------------------------- #
# Feedback → adjusted profile
# --------------------------------------------------------------------------- #
def _append_feedback(current: str, lines: list[str]) -> str:
    """Appends dated feedback lines under a `## Feedback` heading, creating the
    heading if the profile doesn't have one yet. The curator reads these dated
    signals and applies them with weights and time decay during curation."""
    body = (current or "").rstrip()
    if "## Feedback" not in body:
        body = f"{body}\n\n## Feedback" if body else "## Feedback"
    return body + "\n" + "\n".join(lines)


@router.post("/feedback")
def feedback(background_tasks: BackgroundTasks, feedback: str = Form(...)):
    feedback = feedback.strip()
    if feedback:
        current = runtime_config.preferences()
        today = utcnow().date().isoformat()
        # Turn the free text into structured editorial signals (more/less/love/
        # hide + topic). Falls back to recording the raw note verbatim.
        signals = llm.classify_feedback(feedback)
        if signals:
            lines = [f"- {today} · {s['signal']}: {s['topic']}" for s in signals]
        else:
            lines = [f"- {today} · note: {feedback}"]
        runtime_config.set_value("preferences", _append_feedback(current, lines))
        # Rebuild the paper with the adjusted profile.
        background_tasks.add_task(run_pipeline)
    return RedirectResponse(url="/", status_code=303)
