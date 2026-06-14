from datetime import datetime

import httpx

from .base import RawArticle


def fetch_api(url: str, limit: int = 30) -> list[RawArticle]:
    """Generic JSON API fetcher. For now it recognizes Hacker News
    (Algolia). New APIs are added as their own branches here."""
    if "hn.algolia.com" in url or "hacker" in url.lower():
        return _fetch_hn(url, limit)
    raise ValueError(f"Unknown API source: {url}")


def _fetch_hn(url: str, limit: int) -> list[RawArticle]:
    r = httpx.get(url, timeout=20.0, follow_redirects=True)
    r.raise_for_status()
    data = r.json()
    out: list[RawArticle] = []
    for hit in data.get("hits", [])[:limit]:
        object_id = hit.get("objectID")
        link = hit.get("url") or f"https://news.ycombinator.com/item?id={object_id}"
        published = None
        ts = hit.get("created_at_i")
        if ts:
            published = datetime.utcfromtimestamp(ts)
        points = hit.get("points", 0)
        comments = hit.get("num_comments", 0)
        out.append(
            RawArticle(
                url=link,
                title=hit.get("title") or hit.get("story_title") or "(untitled)",
                summary=f"{points} points · {comments} comments on Hacker News.",
                author=hit.get("author", ""),
                published_at=published,
            )
        )
    return out
