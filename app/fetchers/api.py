import html
import re
from datetime import datetime
from typing import Optional

import httpx

from .base import RawArticle

# Links we do NOT want to traverse to — that is the discussion forum itself,
# not an article.
_HN_HOSTS = ("news.ycombinator.com", "ycombinator.com")


def _first_external_link(text: str) -> Optional[str]:
    """Extract the first external link from an HN post body. HN text is
    HTML-entity-escaped (`https:&#x2F;&#x2F;…`) and may contain both `href="…"`
    and bare URLs. Returns None when there is no external link (a pure
    discussion, e.g. Ask HN)."""
    if not text:
        return None
    unescaped = html.unescape(text)
    candidates: list[str] = []
    candidates += re.findall(r'href=["\']([^"\']+)["\']', unescaped, re.I)
    candidates += re.findall(r'https?://[^\s<>"\')]+', unescaped, re.I)
    for raw in candidates:
        link = raw.strip().rstrip(".,;)")
        if not link.lower().startswith(("http://", "https://")):
            continue
        if any(host in link.lower() for host in _HN_HOSTS):
            continue
        return link
    return None


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
        # Prefer the submitted article URL. For text posts (Show HN / Ask HN)
        # that field is empty — traverse to the real article linked in the post
        # body instead of scraping the HN discussion page. No link → it's a pure
        # discussion, so skip it (we never present forum content as an article).
        link = hit.get("url")
        if not link:
            link = _first_external_link(hit.get("story_text") or hit.get("text") or "")
        if not link:
            continue
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
