from concurrent.futures import ThreadPoolExecutor, as_completed

from sqlmodel import select

from .. import progress, runtime_config
from ..config import settings
from ..db import get_session
from ..i18n import lang_prompt_name
from ..llm import translate_batch, translate_headlines_batch
from ..models import Article, Source, utcnow


def _source_langs() -> dict[int, str]:
    with get_session() as s:
        return {src.id: (src.lang or "") for src in s.exec(select(Source)).all()}


def _chunk(targets: list[dict]) -> list[list[dict]]:
    """Pakker artikler i grupper innenfor et tegnbudsjett, så hvert LLM-kall
    oversetter flere artikler men holder seg under en trygg størrelse."""
    budget = settings.translate_batch_chars
    max_items = settings.translate_batch_max
    body_cap = settings.translate_body_max_chars
    chunks: list[list[dict]] = []
    cur: list[dict] = []
    cur_chars = 0
    for t in targets:
        # Budsjettér på det som faktisk sendes (brødtekst kappes ved body_cap).
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
    """Oversetter KUN valgte (kuraterte) artikler som ikke alt er oversatt.
    Batcher flere artikler per kall OG kjører batchene parallelt. Caches på
    artikkelen (translated_at). Re-oversett: nullstill translated_at."""
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
        # Hopp over saker fra kilder på et språk vi ikke oversetter (vises på
        # originalspråk). translated_at røres ikke, så de oversettes automatisk
        # hvis språket senere fjernes fra skip-lista.
        before = len(arts)
        arts = [a for a in arts if runtime_config.should_translate(langs.get(a.source_id, ""))]
        if before - len(arts):
            print(f"[translate] hoppet over {before - len(arts)} på utelatt språk")
        targets = [
            {"id": a.id, "title": a.title, "summary": a.summary or "", "content": a.content or ""}
            for a in arts
        ]

    total = len(targets)
    if not total:
        print("[translate] ingen nye å oversette")
        return 0

    progress.detail(f"0/{total} saker")
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
                print(f"[translate] batch feilet: {e}")
            done += len(chunk)
            progress.detail(f"Oversetter {min(done, total)}/{total}")

    now = utcnow()
    with get_session() as s:
        for t in targets:
            a = s.get(Article, t["id"])
            if not a:
                continue
            res = results.get(t["id"])
            if res:
                a.title_no = res.get("title", t["title"])
                a.summary_no = res.get("summary", t["summary"])
                if t["content"]:
                    a.content_no = res.get("content", t["content"])
            else:
                # Ingen LLM / feilet: behold original så UI har noe å vise.
                a.title_no = t["title"]
                a.summary_no = t["summary"]
                a.content_no = t["content"] or None
            a.translated_lang = plang
            a.translated_at = now
        s.commit()

    print(f"[translate] {total} artikler i {len(chunks)} batch(er), {workers} parallelt")
    return total


def translate_pool_headlines() -> int:
    """Foroversetter tittel+ingress for ferske saker som ikke alt har en
    oversatt tittel — så «flere saker»-lista er på målspråket og åpning rask.
    Brødtekst oversettes først ved åpning (lat). Hopper over utelatte språk."""
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
        print("[translate] ingen nye titler å foroversette")
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
                print(f"[translate] tittel-batch feilet: {e}")

    # Sett kun ved treff. Bom (feil/ingen LLM) lar title_no stå None, så det
    # forsøkes på nytt neste kjør i stedet for å fryse originalen inn.
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

    print(f"[translate] foroversatte {len(results)}/{total} titler i {len(chunks)} batch(er)")
    return len(results)
