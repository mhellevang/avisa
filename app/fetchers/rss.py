from datetime import datetime

import feedparser

from .base import RawArticle, strip_html


def _extract_image(entry) -> str:
    # media:content / media:thumbnail
    for key in ("media_content", "media_thumbnail"):
        media = entry.get(key)
        if media and isinstance(media, list) and media[0].get("url"):
            return media[0]["url"]
    # enclosures (rel=enclosure with image type)
    for link in entry.get("links", []):
        if link.get("rel") == "enclosure" and "image" in link.get("type", ""):
            return link.get("href", "")
    return ""


def _published(entry):
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if parsed:
        # Naive UTC to match the rest of the system.
        return datetime(*parsed[:6])
    return None


def _content(entry) -> str:
    content = entry.get("content")
    if content and isinstance(content, list) and content[0].get("value"):
        return strip_html(content[0]["value"])
    return ""


def fetch_rss(url: str, limit: int = 40) -> list[RawArticle]:
    feed = feedparser.parse(url)
    out: list[RawArticle] = []
    for e in feed.entries[:limit]:
        link = e.get("link")
        if not link:
            continue
        out.append(
            RawArticle(
                url=link,
                title=e.get("title", "(untitled)"),
                summary=strip_html(e.get("summary", "")),
                content=_content(e),
                author=e.get("author", ""),
                image_url=_extract_image(e),
                published_at=_published(e),
            )
        )
    return out
