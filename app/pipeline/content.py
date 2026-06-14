"""Content phase: fetches full text for new stories. Static extraction in parallel
first, then a Playwright fallback (shared browser) for those that yielded too little.

Run in two places in the pipeline:
- fetch_new_content():      capped batch of the newest new stories (openpaper-style)
- fetch_selected_content(): guarantees the front-page stories have full text
"""

from concurrent.futures import ThreadPoolExecutor, as_completed

from sqlmodel import select

from .. import progress
from ..config import settings
from ..db import get_session
from ..i18n import current
from ..fetchers.browser import BrowserSession, playwright_available
from ..fetchers.content import extract_rendered, extract_static
from ..models import Article, utcnow


def _run_fetch(targets: list[tuple[int, str]]) -> int:
    """targets: list of (article_id, url). Fetches full text and writes it back.
    Marks content_fetched_at on all attempted (including those that fail) so we don't
    retry in an endless loop."""
    if not targets:
        return 0

    results: dict[int, dict] = {}  # aid -> {content, image}
    total = len(targets)

    # 1) Static pass in parallel
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
            progress.detail(current("Fetching full text {done}/{total}", done=done, total=total))

    # 2) Playwright fallback for those that didn't get body text statically
    misses = [
        (aid, url) for aid, url in targets if not (results.get(aid) or {}).get("content")
    ]
    if misses and settings.use_playwright and playwright_available():
        try:
            with BrowserSession() as bs:
                for i, (aid, url) in enumerate(misses, 1):
                    progress.detail(current("Rendering JS page {i}/{total} …", i=i, total=len(misses)))
                    res = extract_rendered(bs, url)
                    if res:
                        # Keep any image from the static pass if the rendered one is missing.
                        prev = results.get(aid) or {}
                        if not res.get("image") and prev.get("image"):
                            res["image"] = prev["image"]
                        results[aid] = res
        except Exception as e:
            print(f"[content] browser fallback unavailable: {e}")

    # 3) Write back
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
            # og:image is usually better than the RSS thumbnail — prefer it.
            if res.get("image"):
                obj.image_url = res["image"]
            if res:
                obj.paywalled = bool(res.get("paywalled"))
            obj.content_fetched_at = now
        s.commit()

    print(f"[content] {got_text}/{len(targets)} got full text")
    return got_text


def fetch_new_content(limit: int | None = None) -> int:
    """Full text for the newest stories not attempted before, capped at
    content_fetch_limit. Logs how many were deferred to the next run."""
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
        print(f"[content] {total - len(batch)} stories deferred to the next run (cap {limit})")
    if batch:
        progress.detail(current("0/{total} stories", total=len(batch)))
    return _run_fetch(batch)


def fetch_selected_content() -> int:
    """Guarantees that the curated front-page stories have full text (fetches any
    that fell outside the batch cap above)."""
    with get_session() as s:
        rows = s.exec(
            select(Article).where(
                Article.selected == True,  # noqa: E712
                Article.content_fetched_at == None,  # noqa: E711
            )
        ).all()
        targets = [(a.id, a.url) for a in rows]
    return _run_fetch(targets)
