import hashlib
import json

from sqlmodel import select

from .. import progress
from ..db import get_session
from ..fetchers import api, playwright_list, rss
from ..models import Article, Source, utcnow


def _hash(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


def ingest() -> int:
    """Henter fra alle aktive kilder, deduperer mot url_hash, lagrer nye."""
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
                    print(f"[ingest] ukjent kind '{src.kind}' for {src.name}")
                    continue
            except Exception as e:
                print(f"[ingest] {src.name} feilet: {e}")
                continue

            for raw in raws:
                if not raw.url:
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
    print(f"[ingest] {new_count} nye artikler")
    return new_count
