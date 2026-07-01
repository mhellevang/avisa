import re
from datetime import datetime

import feedparser
import httpx

from .base import USER_AGENT, RawArticle, strip_html

# Trailing boilerplate many feeds append to the <description>: a "read more"
# link (the Guardian's "Continue reading…") or a WordPress "The post … appeared
# first on …" footer. Everything from here on is dropped.
_SUMMARY_TAIL = re.compile(
    r"\s*(?:"
    r"Continue reading"
    r"|Read more"
    r"|Get our breaking news email"      # Guardian newsletter promo
    r"|Sign up (?:to|for)\b"
    r"|Subscribe to\b"
    r"|The post\b.*?\bappeared first on"  # WordPress footer
    r").*$",
    re.IGNORECASE | re.DOTALL,
)


def clean_summary(text: str, max_chars: int = 300) -> str:
    """A feed's <description> is often the standfirst *plus* the opening body
    paragraphs *plus* a 'Continue reading…' link (the Guardian does all three).
    For a lede we want roughly the standfirst: drop the boilerplate tail, then
    cap to the first sentence(s) so it doesn't duplicate the body below it."""
    text = _SUMMARY_TAIL.sub("", text).strip()
    if len(text) <= max_chars:
        return text
    head = text[:max_chars]
    # Prefer cutting at the last sentence boundary; fall back to a hard cut + ellipsis.
    cut = max(head.rfind(". "), head.rfind("! "), head.rfind("? "))
    if cut > 80:
        return head[: cut + 1].strip()
    return head.rstrip() + "…"


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
    # Fetch with httpx and feed the bytes to feedparser. Letting feedparser do
    # the network call itself means urllib with NO timeout — one feed server
    # that accepts the connection but never answers would hang the whole ingest
    # run (and with it the pipeline lock) until the process is restarted.
    # Errors propagate to ingest()'s per-source handler.
    r = httpx.get(url, timeout=20.0, follow_redirects=True, headers={"User-Agent": USER_AGENT})
    r.raise_for_status()
    feed = feedparser.parse(r.content)
    if not feed.entries:
        # A 404/HTML page parses "successfully" to zero entries — say why the
        # source looks empty instead of failing silently.
        why = getattr(feed, "bozo_exception", "") if feed.bozo else "no entries"
        print(f"[rss] empty feed {url}: {why}")
    out: list[RawArticle] = []
    for e in feed.entries[:limit]:
        link = e.get("link")
        if not link:
            continue
        out.append(
            RawArticle(
                url=link,
                title=e.get("title", "(untitled)"),
                summary=clean_summary(strip_html(e.get("summary", ""))),
                content=_content(e),
                author=e.get("author", ""),
                image_url=_extract_image(e),
                published_at=_published(e),
            )
        )
    return out
