"""Content-fase: henter fulltekst for nye saker. Statisk uttrekk i parallell
først, så Playwright-fallback (delt browser) for de som ga for lite.

Kjøres to steder i pipelinen:
- fetch_new_content():      capped batch av nyeste nye saker (openpaper-stil)
- fetch_selected_content(): garanterer at forsidesakene har fulltekst
"""

from concurrent.futures import ThreadPoolExecutor, as_completed

from sqlmodel import select

from .. import progress
from ..config import settings
from ..db import get_session
from ..fetchers.browser import BrowserSession, playwright_available
from ..fetchers.content import extract_rendered, extract_static
from ..models import Article, utcnow


def _run_fetch(targets: list[tuple[int, str]]) -> int:
    """targets: liste av (article_id, url). Henter fulltekst og skriver tilbake.
    Markerer content_fetched_at på alle forsøkte (også de som feiler) så vi ikke
    prøver i evig loop."""
    if not targets:
        return 0

    results: dict[int, dict] = {}  # aid -> {content, image}
    total = len(targets)

    # 1) Statisk pass i parallell
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(extract_static, url): aid for aid, url in targets}
        done = 0
        for f in as_completed(futs):
            aid = futs[f]
            try:
                res = f.result()
            except Exception:
                res = None
            if res:
                results[aid] = res
            done += 1
            progress.detail(f"Henter fulltekst {done}/{total}")

    # 2) Playwright-fallback for de som ikke fikk brødtekst statisk
    misses = [
        (aid, url) for aid, url in targets if not (results.get(aid) or {}).get("content")
    ]
    if misses and settings.use_playwright and playwright_available():
        try:
            with BrowserSession() as bs:
                for i, (aid, url) in enumerate(misses, 1):
                    progress.detail(f"Renderer JS-side {i}/{len(misses)} …")
                    res = extract_rendered(bs, url)
                    if res:
                        # Behold evt. bilde fra statisk pass om rendret mangler.
                        prev = results.get(aid) or {}
                        if not res.get("image") and prev.get("image"):
                            res["image"] = prev["image"]
                        results[aid] = res
        except Exception as e:
            print(f"[content] browser-fallback utilgjengelig: {e}")

    # 3) Skriv tilbake
    now = utcnow()
    got_text = 0
    with get_session() as s:
        for aid, _url in targets:
            obj = s.get(Article, aid)
            if not obj:
                continue
            res = results.get(aid) or {}
            if res.get("content"):
                obj.content = res["content"]
                got_text += 1
            # og:image er som regel bedre enn RSS-thumbnailen — foretrekk den.
            if res.get("image"):
                obj.image_url = res["image"]
            if res:
                obj.paywalled = bool(res.get("paywalled"))
            obj.content_fetched_at = now
        s.commit()

    print(f"[content] {got_text}/{len(targets)} fikk fulltekst")
    return got_text


def fetch_new_content(limit: int | None = None) -> int:
    """Fulltekst for de nyeste sakene som ikke er forsøkt før, kappet til
    content_fetch_limit. Logger hvor mange som ble utsatt til neste kjør."""
    limit = limit or settings.content_fetch_limit
    with get_session() as s:
        rows = s.exec(
            select(Article)
            .where(Article.content_fetched_at == None)  # noqa: E711
            .order_by(Article.fetched_at.desc())
        ).all()
        targets = [(a.id, a.url) for a in rows]

    total = len(targets)
    batch = targets[:limit]
    if total > len(batch):
        print(f"[content] {total - len(batch)} saker utsatt til neste kjør (cap {limit})")
    if batch:
        progress.detail(f"0/{len(batch)} saker")
    return _run_fetch(batch)


def fetch_selected_content() -> int:
    """Garanterer at de kuraterte forsidesakene har fulltekst (henter de som
    evt. falt utenfor batch-cappen over)."""
    with get_session() as s:
        rows = s.exec(
            select(Article).where(
                Article.selected == True,  # noqa: E712
                Article.content_fetched_at == None,  # noqa: E711
            )
        ).all()
        targets = [(a.id, a.url) for a in rows]
    return _run_fetch(targets)
