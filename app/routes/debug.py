"""Debug surface: inspect why an article looks off, and re-run a single one
through translation / full-text fetch to test a fix without rebuilding the
whole edition. Gated behind ADMIN_PASSWORD; pass it as the `X-Admin-Key`
header so it can be called over the Cloudflare tunnel without any host /
Docker access. (Deliberately NOT accepted as a query parameter — that would
leak the admin password into access logs and proxy analytics.) When no
password is set, it's open (same as the rest of the admin surface locally)."""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlmodel import select

from .. import auth, i18n, llm, runtime_config
from ..db import get_session
from ..models import Article, Edition, EditionItem, Source, utcnow
from .common import iso, source_names


def _require_debug_auth(request: Request) -> None:
    if not auth.auth_enabled():
        return
    if auth.is_authed(request):  # logged-in cookie
        return
    if auth.check_password(request.headers.get("X-Admin-Key", "")):
        return
    raise HTTPException(status_code=401, detail="unauthorized")


router = APIRouter(prefix="/debug", dependencies=[Depends(_require_debug_auth)])


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
            "at": iso(a.translated_at),
        },
        "curation": {
            "score": a.score,
            "selected": a.selected,
            "reason": a.curate_reason,
            "deck": a.deck,
        },
        "content_meta": {
            "attempted": a.content_fetched_at is not None,
            "fetched_at": iso(a.content_fetched_at),
            "paywalled": a.paywalled,
            "image_url": a.image_url,
        },
        "timestamps": {
            "published_at": iso(a.published_at),
            "fetched_at": iso(a.fetched_at),
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


def _force_retranslate(s, a: Article) -> bool:
    """Re-translates title/summary/body for one article, overwriting the cache.
    Returns True if anything was actually re-translated; on total failure the
    cache (and translated_at) is left untouched so pipeline retries still work."""
    plang = runtime_config.paper_lang()
    target = i18n.lang_prompt_name(plang)
    ok = False
    fields = llm.translate_fields(a.title, a.summary or "", target=target)
    title = (fields or {}).get("title")
    if isinstance(title, str) and title.strip():
        a.title_no = title
        summary = fields.get("summary")
        a.summary_no = summary if isinstance(summary, str) else a.summary
        ok = True
    if a.content:
        translated = llm.translate_body(a.display_title, a.content, target=target)
        if translated:
            a.content_no = translated
            ok = True
    if ok:
        a.translated_lang = plang
        a.translated_at = utcnow()
        s.commit()
        s.refresh(a)
    return ok


@router.get("/article/{article_id}")
def debug_article(article_id: int, full: int = 0):
    """Dump the full pipeline trace for one article. `?full=1` for whole bodies."""
    with get_session() as s:
        a = s.get(Article, article_id)
        if not a:
            return JSONResponse({"error": "not found"}, status_code=404)
        return JSONResponse(_article_debug(s, a, full=bool(full)))


@router.get("/articles")
def debug_articles(
    q: str = "",
    selected: int | None = None,
    untranslated: int = 0,
    paywalled: int | None = None,
    limit: int = 50,
):
    """Compact list to locate the odd one. Filters: q (title substring),
    selected=0/1, untranslated=1, paywalled=0/1."""
    limit = max(1, min(limit, 200))
    ql = q.lower()
    out = []
    with get_session() as s:
        src_names = source_names(s)
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
                "fetched_at": iso(a.fetched_at),
                "url": a.url,
            })
            if len(out) >= limit:
                break
    return JSONResponse({"count": len(out), "articles": out})


@router.post("/article/{article_id}/retranslate")
def debug_retranslate(article_id: int):
    """Re-run translation for one article synchronously (overwrites the cache),
    then return the fresh trace. Lets you test a translation-prompt fix on a
    single article."""
    if not llm.enabled():
        return JSONResponse({"error": "llm not enabled (no API key)"}, status_code=400)
    with get_session() as s:
        a = s.get(Article, article_id)
        if not a:
            return JSONResponse({"error": "not found"}, status_code=404)
        retranslated = _force_retranslate(s, a)
        result = _article_debug(s, a, full=True)
    result["retranslated"] = retranslated
    return JSONResponse(result)


@router.post("/article/{article_id}/refetch")
def debug_refetch(article_id: int):
    """Re-run full-text extraction for one article (static + Playwright
    fallback) and write it back, then return the fresh trace. Lets you test why
    the body came out odd."""
    with get_session() as s:
        a = s.get(Article, article_id)
        if not a:
            return JSONResponse({"error": "not found"}, status_code=404)
        url, title = a.url, a.title
    # Local import: pulls in the browser stack, keep it off the module load path.
    from ..pipeline.content import _run_fetch

    got = _run_fetch([(article_id, url, title)])
    with get_session() as s:
        a = s.get(Article, article_id)
        if not a:  # deleted while we were fetching (e.g. by pruning)
            return JSONResponse({"error": "not found"}, status_code=404)
        result = _article_debug(s, a, full=True)
    result["refetch_got_text"] = bool(got)
    return JSONResponse(result)


@router.post("/article/{article_id}/reprocess")
def debug_reprocess(article_id: int):
    """Re-extract full text (so a fetcher fix — e.g. inline images — is picked up
    on an already-ingested article) AND re-translate it, in one call. Equivalent
    to /refetch followed by /retranslate. Returns the fresh trace."""
    with get_session() as s:
        a = s.get(Article, article_id)
        if not a:
            return JSONResponse({"error": "not found"}, status_code=404)
        url, title = a.url, a.title
    # Local import: pulls in the browser stack, keep it off the module load path.
    from ..pipeline.content import _run_fetch

    got = _run_fetch([(article_id, url, title)])

    retranslated = False
    with get_session() as s:
        a = s.get(Article, article_id)
        if not a:  # deleted while we were fetching (e.g. by pruning)
            return JSONResponse({"error": "not found"}, status_code=404)
        if llm.enabled():
            retranslated = _force_retranslate(s, a)
        result = _article_debug(s, a, full=True)
    result["refetch_got_text"] = bool(got)
    result["retranslated"] = retranslated
    return JSONResponse(result)


@router.post("/backfill-summaries")
def debug_backfill_summaries():
    """One-off maintenance: re-clean already-stored ledes (strip 'Continue
    reading…' tails and over-long RSS descriptions) on both the original and the
    translated summary. New articles are cleaned at ingest; this fixes rows that
    predate that. Idempotent — safe to run repeatedly."""
    from ..fetchers.rss import clean_summary

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
