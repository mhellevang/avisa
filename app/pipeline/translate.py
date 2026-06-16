from concurrent.futures import ThreadPoolExecutor, as_completed

from sqlmodel import select

from .. import progress, runtime_config
from ..config import settings
from ..db import get_session
from ..i18n import current, lang_prompt_name
from ..llm import translate_batch, translate_headlines_batch
from ..models import Article, Source, utcnow


def _source_langs() -> dict[int, str]:
    with get_session() as s:
        return {src.id: (src.lang or "") for src in s.exec(select(Source)).all()}


def _chunk(targets: list[dict]) -> list[list[dict]]:
    """Packs articles into groups within a character budget, so each LLM call
    translates several articles but stays under a safe size."""
    budget = settings.translate_batch_chars
    max_items = settings.translate_batch_max
    body_cap = settings.translate_body_max_chars
    chunks: list[list[dict]] = []
    cur: list[dict] = []
    cur_chars = 0
    for t in targets:
        # Budget on what is actually sent (body text is capped at body_cap).
        size = min(len(t["content"]), body_cap) + len(t["summary"])
        if cur and (cur_chars + size > budget or len(cur) >= max_items):
            chunks.append(cur)
            cur, cur_chars = [], 0
        cur.append(t)
        cur_chars += size
    if cur:
        chunks.append(cur)
    return chunks


def translate() -> int:
    """Translates ONLY selected (curated) articles that aren't already translated.
    Batches several articles per call AND runs the batches in parallel. Cached on
    the article (translated_at). To re-translate: reset translated_at."""
    plang = runtime_config.paper_lang()
    target = lang_prompt_name(plang)
    langs = _source_langs()
    with get_session() as s:
        arts = s.exec(
            select(Article).where(
                Article.selected == True,  # noqa: E712
                Article.translated_at == None,  # noqa: E711
            )
        ).all()
        # Skip stories from sources in a language we don't translate (shown in the
        # original language). translated_at is left untouched, so they are translated
        # automatically if the language is later removed from the skip list.
        before = len(arts)
        arts = [a for a in arts if runtime_config.should_translate(langs.get(a.source_id, ""))]
        if before - len(arts):
            print(f"[translate] skipped {before - len(arts)} in excluded language")
        targets = [
            {"id": a.id, "title": a.title, "summary": a.summary or "", "content": a.content or ""}
            for a in arts
        ]

    total = len(targets)
    if not total:
        print("[translate] nothing new to translate")
        return 0

    progress.detail(current("0/{total} stories", total=total))
    chunks = _chunk(targets)
    results: dict[int, dict] = {}
    done = 0
    workers = max(1, min(settings.translate_concurrency, len(chunks)))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(translate_batch, c, target): c for c in chunks}
        for f in as_completed(futs):
            chunk = futs[f]
            try:
                results.update(f.result())
            except Exception as e:
                print(f"[translate] batch failed: {e}")
            done += len(chunk)
            progress.detail(current("Translating {done}/{total}", done=min(done, total), total=total))

    now = utcnow()
    translated = 0
    with get_session() as s:
        for t in targets:
            res = results.get(t["id"])
            if not res:
                # Batch failed / no LLM: leave translated_at = None so the article is
                # retried on the next run instead of freezing the original-language
                # text in. The UI falls back to the original (display_title, and the
                # article page translates inline / lazy-loads the body on open).
                continue
            a = s.get(Article, t["id"])
            if not a:
                continue
            a.title_no = res.get("title", t["title"])
            a.summary_no = res.get("summary", t["summary"])
            if t["content"]:
                a.content_no = res.get("content", t["content"])
            a.translated_lang = plang
            a.translated_at = now
            translated += 1
        s.commit()

    print(f"[translate] {translated}/{total} translated in {len(chunks)} batch(es), {workers} in parallel")
    return translated


def translate_pool_headlines() -> int:
    """Pre-translates title+lead for recent stories that don't already have a
    translated title — so the "more stories" list is in the target language and opens
    quickly. Body text is translated on first open (lazy). Skips excluded languages."""
    plang = runtime_config.paper_lang()
    target = lang_prompt_name(plang)
    langs = _source_langs()
    with get_session() as s:
        arts = s.exec(
            select(Article)
            .where(Article.title_no == None)  # noqa: E711
            .where(Article.paywalled == False)  # noqa: E712
            .order_by(Article.fetched_at.desc())
            .limit(settings.translate_headlines_limit)
        ).all()
        targets = [
            {"id": a.id, "title": a.title, "summary": a.summary or ""}
            for a in arts
            if runtime_config.should_translate(langs.get(a.source_id, ""))
        ]

    total = len(targets)
    if not total:
        print("[translate] no new titles to pre-translate")
        return 0

    max_items = settings.translate_batch_max
    chunks = [targets[i : i + max_items] for i in range(0, total, max_items)]
    results: dict[int, dict] = {}
    workers = max(1, min(settings.translate_concurrency, len(chunks)))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(translate_headlines_batch, c, target): c for c in chunks}
        for f in as_completed(futs):
            try:
                results.update(f.result())
            except Exception as e:
                print(f"[translate] title batch failed: {e}")

    # Only set on a hit. A miss (error/no LLM) leaves title_no as None, so it is
    # retried on the next run instead of freezing the original in.
    with get_session() as s:
        for t in targets:
            res = results.get(t["id"])
            if not res:
                continue
            a = s.get(Article, t["id"])
            if not a:
                continue
            a.title_no = res.get("title", t["title"])
            a.summary_no = res.get("summary", t["summary"])
            a.translated_lang = plang
        s.commit()

    print(f"[translate] pre-translated {len(results)}/{total} titles in {len(chunks)} batch(es)")
    return len(results)
