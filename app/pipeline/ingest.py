import hashlib
import json

from sqlmodel import select

from .. import progress
from ..db import get_session
from ..fetchers import api, playwright_list, rss
from ..models import Article, Source, utcnow


def _hash(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


# URL fragments that mark a page as something other than an editorial story:
#   /live/, /liveblog/ — live-blog stubs (NYT/Guardian/Al Jazeera), just a
#                        pointer into a feed
#   /video/, /videos/  — video pages (e.g. Al Jazeera /video/newsfeed/, BBC
#                        /news/videos/): the substance is the clip, the "body"
#                        is only a one-line caption
#   puzzles  — crosswords/games (e.g. New Yorker /puzzles-and-games-dept/),
#              whose "body" is only clue lists, not prose
#   /cartoons/ — New Yorker daily cartoon, whose "body" is just the caption
_SKIP_URL_MARKERS = (
    "/live/",
    "/liveblog/",
    "/live-blog/",
    "liveticker",     # German/Swiss tickers (blick.ch, srf.ch, …)
    "/direkte/",      # Norwegian live coverage (NRK, VG, …)
    "live-updates",   # AP/NYT "…-live-updates" slugs
    "/video/",
    "/videos/",
    "/puzzles-and-games-dept/",
    "/crossword/",
    "/cartoons/",
)


def _is_non_article(url: str) -> bool:
    return any(marker in url for marker in _SKIP_URL_MARKERS)


def ingest() -> int:
    """Fetches from all active sources, dedupes against url_hash, stores new ones."""
    new_count = 0
    with get_session() as s:
        sources = s.exec(select(Source).where(Source.enabled == True)).all()  # noqa: E712
        for i, src in enumerate(sources, 1):
            progress.detail(f"{src.name} ({i}/{len(sources)})")
            try:
                if src.kind == "rss":
                    raws = rss.fetch_rss(src.url)
                elif src.kind == "api":
                    raws = api.fetch_api(src.url)
                elif src.kind == "playwright":
                    cfg = json.loads(src.config) if src.config else {}
                    raws = playwright_list.fetch_playwright_listing(src.url, cfg)
                else:
                    print(f"[ingest] unknown kind '{src.kind}' for {src.name}")
                    continue
            except Exception as e:
                print(f"[ingest] {src.name} failed: {e}")
                continue

            for raw in raws:
                if not raw.url:
                    continue
                # Live-blog stubs and puzzle/crossword pages aren't editorial
                # stories — their "body" is empty or just clue lists. Skip them.
                if _is_non_article(raw.url):
                    continue
                h = _hash(raw.url)
                exists = s.exec(
                    select(Article).where(Article.url_hash == h)
                ).first()
                if exists:
                    continue
                s.add(
                    Article(
                        source_id=src.id,
                        url=raw.url,
                        url_hash=h,
                        title=raw.title,
                        summary=raw.summary,
                        content=raw.content,
                        author=raw.author,
                        image_url=raw.image_url,
                        published_at=raw.published_at,
                        fetched_at=utcnow(),
                        section=src.section,
                    )
                )
                new_count += 1
        s.commit()
    print(f"[ingest] {new_count} new articles")
    return new_count
