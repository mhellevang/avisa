from concurrent.futures import ThreadPoolExecutor, as_completed

from sqlmodel import select

from .. import progress
from ..config import settings
from ..db import get_session
from ..llm import translate_batch
from ..models import Article, utcnow


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
    with get_session() as s:
        arts = s.exec(
            select(Article).where(
                Article.selected == True,  # noqa: E712
                Article.translated_at == None,  # noqa: E711
            )
        ).all()
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
        futs = {ex.submit(translate_batch, c): c for c in chunks}
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
            a.translated_at = now
        s.commit()

    print(f"[translate] {total} artikler i {len(chunks)} batch(er), {workers} parallelt")
    return total
