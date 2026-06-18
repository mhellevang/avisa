import json
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from markupsafe import escape
from sqlmodel import select

from . import auth, catalog, i18n, llm, progress, runtime_config, scheduler
from .config import settings
from .db import get_session
from .fetchers import discover
from .models import Article, Edition, EditionItem, Source, utcnow
from .pipeline import run_pipeline

router = APIRouter()

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

def domain(url: str) -> str:
    try:
        from urllib.parse import urlparse

        return urlparse(url).netloc.replace("www.", "")
    except Exception:
        return ""


# An inline image ![alt](url) — only http(s) srcs become <img>. Must run before
# _MD_LINK, or the link regex matches the [alt](url) inside it and leaves a stray '!'.
_MD_IMAGE = re.compile(r"!\[([^\]]*)\]\((https?://[^\s)]+)\)")
_MD_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^\s)]+)\)")
# Any leftover markdown link (relative, mailto:, …) — kept as plain text so a
# raw '[text](url)' never shows. Run after _MD_LINK turns http(s) into anchors.
_MD_LINK_ANY = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_MD_BOLD = re.compile(r"\*\*(.+?)\*\*")
_MD_ITALIC = re.compile(r"(?<![\*\w])\*(?!\s)(.+?)(?<!\s)\*(?!\*)")
_MD_CODE = re.compile(r"`([^`]+)`")
# A markdown table separator row, e.g. '|---|:--:|'. Each cell is dashes with
# optional leading/trailing colon for alignment.
_TABLE_CELL = re.compile(r":?-+:?")


def _inline_md(text: str) -> str:
    """Renders inline markdown in already-HTML-escaped text: links, bold,
    italic, inline code. Only http(s) links become anchors; other links keep
    their text only."""
    text = _MD_IMAGE.sub(
        lambda m: f'<img src="{m.group(2)}" alt="{m.group(1)}" loading="lazy" onerror="this.remove()">',
        text,
    )
    text = _MD_LINK.sub(
        lambda m: f'<a href="{m.group(2)}" target="_blank" rel="noopener">{m.group(1)}</a>',
        text,
    )
    text = _MD_LINK_ANY.sub(r"\1", text)
    text = _MD_BOLD.sub(r"<strong>\1</strong>", text)
    text = _MD_ITALIC.sub(r"<em>\1</em>", text)
    text = _MD_CODE.sub(r"<code>\1</code>", text)
    return text


def _table_cells(line: str) -> list[str]:
    """Splits a markdown table row '| a | b |' into ['a', 'b']."""
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [c.strip() for c in line.split("|")]


def _is_table_sep(line: str) -> bool:
    cells = _table_cells(line)
    return bool(cells) and all(c and _TABLE_CELL.fullmatch(c) for c in cells)


def body_html(md: str) -> str:
    """Renders the stored body to HTML. Handles a small markdown subset: '```'
    fenced code blocks, '##' headings, '-'/'*' bullet lists, '|' tables, and
    inline bold/italic/code/links. Each non-blank line is its own paragraph —
    trafilatura never wraps a paragraph across lines, and this is robust to
    bodies that separate paragraphs with a single newline (older plain-text
    extraction) as well as blank lines. All text is escaped before markdown is
    applied, so source HTML can't leak."""
    if not md:
        return ""
    html: list[str] = []
    items: list[str] = []
    code: list[str] | None = None  # accumulating fenced-code lines when not None
    table: list[str] = []  # accumulating consecutive '|' rows

    def flush_list():
        if items:
            lis = "".join(f"<li>{_inline_md(escape(i))}</li>" for i in items)
            html.append(f"<ul>{lis}</ul>")
            items.clear()

    def flush_code():
        nonlocal code
        if code is not None:
            body = escape("\n".join(code))
            html.append(f"<pre><code>{body}</code></pre>")
            code = None

    def flush_table():
        if not table:
            return
        rows = table[:]
        table.clear()
        # A real table has a dashes separator as its second row; without it the
        # '|' lines are just prose, so fall back to paragraphs.
        if len(rows) >= 2 and _is_table_sep(rows[1]):
            head = "".join(f"<th>{_inline_md(escape(c))}</th>" for c in _table_cells(rows[0]))
            cells = "".join(
                "<tr>" + "".join(f"<td>{_inline_md(escape(c))}</td>" for c in _table_cells(r)) + "</tr>"
                for r in rows[2:]
            )
            html.append(f"<table><thead><tr>{head}</tr></thead><tbody>{cells}</tbody></table>")
        else:
            for r in rows:
                html.append(f"<p>{_inline_md(escape(r.strip()))}</p>")

    for raw in md.split("\n"):
        # A '```' fence opens or closes a code block. Inside one, lines are kept
        # verbatim (indentation preserved, no inline markdown) until the fence.
        if raw.strip().startswith("```"):
            if code is None:
                flush_list()
                flush_table()
                code = []
            else:
                flush_code()
            continue
        if code is not None:
            code.append(raw)
            continue
        line = raw.strip()
        if not line:
            flush_list()
            flush_table()
            continue
        if line.startswith("|"):
            flush_list()
            table.append(raw)
            continue
        flush_table()  # any non-table line closes a pending table
        if line.startswith("#"):
            flush_list()
            level = len(line) - len(line.lstrip("#"))
            tag = "h2" if level <= 2 else "h3"
            html.append(f"<{tag}>{_inline_md(escape(line.lstrip('#').strip()))}</{tag}>")
        elif line[:2] in ("- ", "* "):
            items.append(line[2:].strip())
        else:
            flush_list()
            html.append(f"<p>{_inline_md(escape(line))}</p>")
    flush_code()
    flush_list()
    flush_table()
    return "".join(html)


def _ui_lang() -> str:
    return i18n.ui_lang(runtime_config.paper_lang())


def t(key: str) -> str:
    """UI translation bound to the paper's current target language."""
    return i18n.t(key, _ui_lang())


# Date helpers localized to the paper's current UI language. Template names are
# kept (no_date / no_datetime) so the markup doesn't need to change.
templates.env.globals["no_date"] = lambda dt: i18n.fmt_date(dt, _ui_lang())
templates.env.globals["no_datetime"] = lambda dt: i18n.fmt_datetime(dt, _ui_lang())
templates.env.globals["domain"] = domain
# Callable so the title can change at runtime (settings/wizard).
templates.env.globals["paper_title"] = runtime_config.paper_title
templates.env.globals["t"] = t
templates.env.globals["ui_lang"] = _ui_lang
templates.env.globals["body_html"] = body_html
# Naive-UTC timestamp -> the paper's local timezone, for the morning/evening
# label and any other place a template needs the local wall-clock hour.
templates.env.globals["to_local"] = i18n.to_local


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
            secure=settings.cookie_secure,
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
            if res:
                a.title_no = res.get("title", a.title)
                a.summary_no = res.get("summary", a.summary)
            else:
                a.title_no, a.summary_no = a.title, a.summary
            a.translated_lang = plang
            s.commit()
            s.refresh(a)

        body_pending = do_translate and bool(a.content) and a.content_no is None

        # Previous/next within the latest edition, so you can page through it like a newspaper.
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
        src = s.get(Source, a.source_id)
        do_translate = llm.enabled() and runtime_config.should_translate(src.lang if src else "")
        if do_translate and a.content and a.content_no is None:
            plang = runtime_config.paper_lang()
            translated = llm.translate_body(
                a.display_title, a.content, target=i18n.lang_prompt_name(plang)
            )
            a.content_no = translated or a.content
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
    Immediate, no on-the-fly processing."""
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
    return JSONResponse(snap)


# ---------------------------------------------------------------------------
# Debug surface: inspect why an article looks off, and re-run a single one
# through translation / full-text fetch to test a fix without rebuilding the
# whole edition. Gated behind ADMIN_PASSWORD; pass it as the `X-Admin-Key`
# header or `?key=` so it can be called over the Cloudflare tunnel without any
# host / Docker access. When no password is set, it's open (same as the rest
# of the admin surface locally).
# ---------------------------------------------------------------------------


def _debug_auth_ok(request: Request) -> bool:
    if not auth.auth_enabled():
        return True
    if auth.is_authed(request):  # logged-in cookie
        return True
    key = request.headers.get("X-Admin-Key") or request.query_params.get("key", "")
    return auth.check_password(key)


def _iso(dt):
    return dt.isoformat() if dt else None


def _article_debug(s, a: Article, full: bool = False) -> dict:
    """Full pipeline trace for one article: original vs. translated text, all
    curation/translation metadata, source config, and the derived flags that
    decide how it's rendered. `full` returns whole bodies; otherwise excerpts."""
    src = s.get(Source, a.source_id)
    plang = runtime_config.paper_lang()
    do_translate = llm.enabled() and runtime_config.should_translate(src.lang if src else "")

    placement = None
    ed = s.exec(select(Edition).order_by(Edition.id.desc())).first()
    if ed:
        ei = s.exec(
            select(EditionItem).where(
                EditionItem.edition_id == ed.id,
                EditionItem.article_id == a.id,
            )
        ).first()
        if ei:
            placement = {"edition_id": ed.id, "rank": ei.rank, "slot": ei.slot}

    def body(value):
        value = value or ""
        return value if full else value[:2000]

    return {
        "id": a.id,
        "url": a.url,
        "section": a.section,
        "source": {
            "id": src.id,
            "name": src.name,
            "kind": src.kind,
            "lang": src.lang,
            "section": src.section,
        } if src else None,
        "original": {
            "title": a.title,
            "summary": a.summary,
            "content": body(a.content),
            "content_len": len(a.content or ""),
        },
        "translated": {
            "title": a.title_no,
            "summary": a.summary_no,
            "content": body(a.content_no),
            "content_len": len(a.content_no or ""),
            "lang": a.translated_lang,
            "at": _iso(a.translated_at),
        },
        "curation": {
            "score": a.score,
            "selected": a.selected,
            "reason": a.curate_reason,
            "deck": a.deck,
        },
        "content_meta": {
            "attempted": a.content_fetched_at is not None,
            "fetched_at": _iso(a.content_fetched_at),
            "paywalled": a.paywalled,
            "image_url": a.image_url,
        },
        "timestamps": {
            "published_at": _iso(a.published_at),
            "fetched_at": _iso(a.fetched_at),
        },
        "computed": {
            "paper_lang": plang,
            "llm_enabled": llm.enabled(),
            "do_translate": do_translate,
            "needs_body_translation": bool(do_translate and a.content and a.content_no is None),
        },
        "edition_placement": placement,
        "truncated": (not full) and (
            len(a.content or "") > 2000 or len(a.content_no or "") > 2000
        ),
    }


@router.get("/debug/article/{article_id}")
def debug_article(request: Request, article_id: int, full: int = 0):
    """Dump the full pipeline trace for one article. `?full=1` for whole bodies."""
    if not _debug_auth_ok(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    with get_session() as s:
        a = s.get(Article, article_id)
        if not a:
            return JSONResponse({"error": "not found"}, status_code=404)
        return JSONResponse(_article_debug(s, a, full=bool(full)))


@router.get("/debug/articles")
def debug_articles(
    request: Request,
    q: str = "",
    selected: int | None = None,
    untranslated: int = 0,
    paywalled: int | None = None,
    limit: int = 50,
):
    """Compact list to locate the odd one. Filters: q (title substring),
    selected=0/1, untranslated=1, paywalled=0/1."""
    if not _debug_auth_ok(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    limit = max(1, min(limit, 200))
    ql = q.lower()
    out = []
    with get_session() as s:
        src_names = _source_names(s)
        rows = s.exec(select(Article).order_by(Article.fetched_at.desc())).all()
        for a in rows:
            if selected is not None and a.selected != bool(selected):
                continue
            if untranslated and a.title_no is not None:
                continue
            if paywalled is not None and a.paywalled != bool(paywalled):
                continue
            if ql and ql not in (a.title or "").lower() and ql not in (a.title_no or "").lower():
                continue
            out.append({
                "id": a.id,
                "source": src_names.get(a.source_id, ""),
                "section": a.section,
                "title": a.display_title,
                "selected": a.selected,
                "score": a.score,
                "translated_lang": a.translated_lang,
                "translated": a.title_no is not None,
                "content_len": len(a.content or ""),
                "paywalled": a.paywalled,
                "fetched_at": _iso(a.fetched_at),
                "url": a.url,
            })
            if len(out) >= limit:
                break
    return JSONResponse({"count": len(out), "articles": out})


@router.post("/debug/article/{article_id}/retranslate")
def debug_retranslate(request: Request, article_id: int):
    """Re-run translation for one article synchronously (clears the cache and
    re-translates title/summary/body), then return the fresh trace. Lets you
    test a translation-prompt fix on a single article."""
    if not _debug_auth_ok(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    if not llm.enabled():
        return JSONResponse({"error": "llm not enabled (no API key)"}, status_code=400)
    with get_session() as s:
        a = s.get(Article, article_id)
        if not a:
            return JSONResponse({"error": "not found"}, status_code=404)
        plang = runtime_config.paper_lang()
        target = i18n.lang_prompt_name(plang)
        fields = llm.translate_fields(a.title, a.summary or "", target=target)
        if fields:
            a.title_no = fields.get("title", a.title)
            a.summary_no = fields.get("summary", a.summary)
        else:
            a.title_no, a.summary_no = a.title, a.summary
        if a.content:
            translated = llm.translate_body(a.display_title, a.content, target=target)
            a.content_no = translated or a.content
        a.translated_lang = plang
        a.translated_at = utcnow()
        s.commit()
        s.refresh(a)
        return JSONResponse(_article_debug(s, a, full=True))


@router.post("/debug/article/{article_id}/refetch")
def debug_refetch(request: Request, article_id: int):
    """Re-run full-text extraction for one article (static + Playwright
    fallback) and write it back, then return the fresh trace. Lets you test why
    the body came out odd."""
    if not _debug_auth_ok(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    with get_session() as s:
        a = s.get(Article, article_id)
        if not a:
            return JSONResponse({"error": "not found"}, status_code=404)
        url = a.url
    # Local import: pulls in the browser stack, keep it off the module load path.
    from .pipeline.content import _run_fetch

    got = _run_fetch([(article_id, url)])
    with get_session() as s:
        a = s.get(Article, article_id)
        result = _article_debug(s, a, full=True)
    result["refetch_got_text"] = bool(got)
    return JSONResponse(result)


@router.post("/debug/article/{article_id}/reprocess")
def debug_reprocess(request: Request, article_id: int):
    """Re-extract full text (so a fetcher fix — e.g. inline images — is picked up
    on an already-ingested article) AND re-translate it, in one call. Equivalent
    to /refetch followed by /retranslate. Returns the fresh trace."""
    if not _debug_auth_ok(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    with get_session() as s:
        a = s.get(Article, article_id)
        if not a:
            return JSONResponse({"error": "not found"}, status_code=404)
        url = a.url
    # Local import: pulls in the browser stack, keep it off the module load path.
    from .pipeline.content import _run_fetch

    got = _run_fetch([(article_id, url)])

    retranslated = False
    if llm.enabled():
        plang = runtime_config.paper_lang()
        target = i18n.lang_prompt_name(plang)
        with get_session() as s:
            a = s.get(Article, article_id)
            fields = llm.translate_fields(a.title, a.summary or "", target=target)
            if fields:
                a.title_no = fields.get("title", a.title)
                a.summary_no = fields.get("summary", a.summary)
            else:
                a.title_no, a.summary_no = a.title, a.summary
            if a.content:
                translated = llm.translate_body(a.display_title, a.content, target=target)
                a.content_no = translated or a.content
            a.translated_lang = plang
            a.translated_at = utcnow()
            s.commit()
            retranslated = True

    with get_session() as s:
        a = s.get(Article, article_id)
        result = _article_debug(s, a, full=True)
    result["refetch_got_text"] = bool(got)
    result["retranslated"] = retranslated
    return JSONResponse(result)


@router.post("/debug/backfill-summaries")
def debug_backfill_summaries(request: Request):
    """One-off maintenance: re-clean already-stored ledes (strip 'Continue
    reading…' tails and over-long RSS descriptions) on both the original and the
    translated summary. New articles are cleaned at ingest; this fixes rows that
    predate that. Idempotent — safe to run repeatedly."""
    if not _debug_auth_ok(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    from .fetchers.rss import clean_summary

    scanned = changed = 0
    with get_session() as s:
        for a in s.exec(select(Article)).all():
            scanned += 1
            new_summary = clean_summary(a.summary or "")
            new_summary_no = clean_summary(a.summary_no) if a.summary_no else a.summary_no
            if new_summary != (a.summary or "") or new_summary_no != a.summary_no:
                a.summary = new_summary
                if a.summary_no is not None:
                    a.summary_no = new_summary_no
                changed += 1
        s.commit()
    return JSONResponse({"scanned": scanned, "changed": changed})


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


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #
def _label(item: dict) -> str:
    return item["label_no"] if _ui_lang() == "no" else item["label_en"]


def _translate_summary(sources, plang: str, skip: set[str]) -> dict:
    """What translation will actually do, derived from the sources' languages,
    the paper language and the skip list. Lets the UI explain it plainly."""
    translated, kept = set(), set()
    for src in sources:
        if not src.enabled:
            continue
        sl = (src.lang or "").strip().lower()
        if not sl or sl == plang:
            kept.add(sl or plang)
        elif sl in skip:
            kept.add(sl)
        else:
            translated.add(sl)
    return {
        "translated": sorted((c, i18n.lang_label(c)) for c in translated if c),
        "kept": sorted((c, i18n.lang_label(c)) for c in kept if c),
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
    paper_lang: str = Form("no"),
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
    new_lang = (paper_lang or "no").strip().lower()
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
                done.append(i18n.current("added «{name}»", name=prop["name"]))
                rebuild = True
            else:
                done.append(i18n.current("couldn't find a source for «{query}»", query=act.get("query", "")))
        elif kind in ("remove_source", "enable_source", "disable_source"):
            name = (act.get("name") or "").strip().lower()
            with get_session() as s:
                match = next(
                    (x for x in s.exec(select(Source)).all() if x.name.lower() == name), None
                )
                if not match:
                    done.append(i18n.current("couldn't find the source «{name}»", name=act.get("name", "")))
                elif kind == "remove_source":
                    s.delete(match); s.commit()
                    done.append(i18n.current("removed «{name}»", name=match.name)); rebuild = True
                else:
                    match.enabled = (kind == "enable_source"); s.commit()
                    key = "turned on «{name}»" if match.enabled else "turned off «{name}»"
                    done.append(i18n.current(key, name=match.name))
                    rebuild = True
        elif kind == "set_refinement":
            runtime_config.set_value("preferences_extra", str(act.get("value") or "").strip())
            runtime_config.rebuild_preferences()
            done.append(i18n.current("updated the profile")); rebuild = True
        elif kind == "set_title" and act.get("value"):
            runtime_config.set_value("paper_title", str(act["value"]).strip())
            done.append(i18n.current("set the title to «{value}»", value=act["value"]))
        elif kind == "set_front_page_size" and act.get("value"):
            try:
                runtime_config.set_value("front_page_size", str(max(1, int(act["value"]))))
                done.append(i18n.current("set the front-page size to {value}", value=int(act["value"]))); rebuild = True
            except (TypeError, ValueError):
                pass
        elif kind == "set_poll_minutes" and act.get("value"):
            try:
                poll = max(1, int(act["value"]))
                runtime_config.set_value("poll_minutes", str(poll))
                scheduler.reschedule(poll)
                done.append(i18n.current("set the poll interval to {value} min", value=poll))
            except (TypeError, ValueError):
                pass
        elif kind in ("select_topic", "deselect_topic") and act.get("key"):
            key = str(act["key"]).strip().lower()
            by_key = {t["key"]: t for t in catalog.TOPICS}
            chosen = runtime_config.topic_keys()
            if key in by_key and kind == "select_topic" and key not in chosen:
                chosen.append(key)
                runtime_config.set_value("profile_topics", ",".join(chosen))
                runtime_config.rebuild_preferences()
                done.append(i18n.current("added the topic «{label}»", label=_label(by_key[key]))); rebuild = True
            elif key in by_key and kind == "deselect_topic" and key in chosen:
                chosen = [k for k in chosen if k != key]
                runtime_config.set_value("profile_topics", ",".join(chosen))
                runtime_config.rebuild_preferences()
                done.append(i18n.current("removed the topic «{key}»", key=_label(by_key[key]))); rebuild = True

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


@router.post("/sources/discover")
def source_discover(query: str = Form(...)):
    """Smart source setup: figure out a bare URL/domain and add it."""
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
            f"✓ Added «{prop['name']}» — {prop['kind'].upper()} in {prop['section']}, "
            f"{prop['entries']} stories found."
        )
    else:
        msg = "⚠ " + prop.get("reason", "Couldn't figure out the source.")
    return RedirectResponse(url=f"/settings?msg={quote(msg)}", status_code=303)


@router.post("/sources/catalog-add")
def source_catalog_add(
    region: str = Form(""),
    urls: list[str] = Form(default=[]),
):
    """Add the checked catalogue sources, skipping any already present."""
    region = (region or "").strip().lower()
    if region:
        runtime_config.set_value("home_region", region)
    by_url = {c["url"]: c for c in catalog.SOURCES}
    added = 0
    with get_session() as s:
        have = {(src.url or "").strip() for src in s.exec(select(Source)).all()}
        for u in urls:
            c = by_url.get(u)
            if not c or c["url"] in have:
                continue
            s.add(
                Source(
                    name=c["name"], kind=c["kind"], url=c["url"],
                    section=c["section"], lang=c["lang"], enabled=True,
                )
            )
            have.add(c["url"])
            added += 1
        s.commit()
    msg = i18n.current("Added {n} sources.", n=added) if added else i18n.current("No new sources added.")
    return RedirectResponse(url=f"/settings?msg={quote(msg)}#sources", status_code=303)


@router.post("/sources/add")
def source_add(
    name: str = Form(...),
    kind: str = Form(...),
    url: str = Form(...),
    section: str = Form("News"),
    lang: str = Form("en"),
    config: str = Form(""),
):
    cfg = None
    config = config.strip()
    if config:
        try:
            json.loads(config)  # validate
            cfg = config
        except json.JSONDecodeError:
            cfg = None  # ignore invalid JSON rather than failing
    with get_session() as s:
        s.add(
            Source(
                name=name.strip(),
                kind=kind.strip(),
                url=url.strip(),
                section=(section.strip() or "News"),
                lang=(lang.strip().lower() or "en"),
                enabled=True,
                config=cfg,
            )
        )
        s.commit()
    return RedirectResponse(url="/settings", status_code=303)


@router.post("/sources/{source_id}/lang")
def source_set_lang(source_id: int, lang: str = Form(...)):
    with get_session() as s:
        src = s.get(Source, source_id)
        if src:
            src.lang = lang.strip().lower() or src.lang
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
