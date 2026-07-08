import re
from datetime import datetime

import feedparser
import httpx

from ..config import settings
from .base import USER_AGENT, RawArticle, strip_html

# Trailing boilerplate many feeds append to the <description>: a "read more"
# link (the Guardian's "Continue reading…") or a WordPress "The post … appeared
# first on …" footer. These are unambiguous link/footer phrases, dropped anywhere.
_SUMMARY_TAIL = re.compile(
    r"\s*(?:"
    r"Continue reading"
    r"|Read more\b"
    r"|Get our breaking news email"      # Guardian newsletter promo
    r"|The post\b.*?\bappeared first on"  # WordPress footer
    r").*$",
    re.IGNORECASE | re.DOTALL,
)
# "Sign up …" / "Subscribe to …" are also newsletter promos, but the words occur
# mid-sentence in real prose too ("told to subscribe to the new fund before
# Friday"). Only drop them when they START a trailing clause — i.e. right after
# a sentence end or a line break — so a legitimate summary isn't truncated. The
# boundary char is kept.
_SUMMARY_PROMO = re.compile(
    r"([.!?»”)\]])\s*(?:Sign up (?:to|for)|Subscribe (?:to|now)|Sign up now)\b.*$",
    re.IGNORECASE | re.DOTALL,
)
# WordPress-style comment/footer tail on RSS content:encoded teasers (Ars
# Technica appends "Read full article Comments").
_CONTENT_TAIL = re.compile(r"\s*Read full article(?:\s+Comments)?\s*$", re.I)
# Amedia/Digi premium marker prepended to feed titles.
_TITLE_TAG = re.compile(r"^\s*\[(?:ekstra|pluss|abonnent|\+)\]\s*", re.I)


def clean_summary(text: str, max_chars: int = 300) -> str:
    """A feed's <description> is often the standfirst *plus* the opening body
    paragraphs *plus* a 'Continue reading…' link (the Guardian does all three).
    For a lede we want roughly the standfirst: drop the boilerplate tail, then
    cap to the first sentence(s) so it doesn't duplicate the body below it."""
    # A lede is one line — collapse the block-boundary newlines strip_html keeps.
    text = " ".join(text.split())
    text = _SUMMARY_TAIL.sub("", text)
    text = _SUMMARY_PROMO.sub(r"\1", text).strip()
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
    """The RSS content:encoded body, if any. Kept ONLY when it is long enough to
    be a real article body: a short value is a teaser or a lone photo caption
    (e.g. Le Monde ships just the lead image's <figcaption>), and storing that as
    the body makes a stub masquerade as a full article — better to leave it
    empty so full-text fetch fills it (or the reader shows a clean 'read at
    source' stub). Ars-style teasers (~1k chars) clear the floor and are kept."""
    content = entry.get("content")
    if not (content and isinstance(content, list) and content[0].get("value")):
        return ""
    text = _CONTENT_TAIL.sub("", strip_html(content[0]["value"])).strip()
    if len(text) < settings.content_min_chars:
        return ""
    return text


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
        # Drop list/figure blocks from the summary HTML first: feeds (the
        # Guardian) splice a related-articles <ul> into <description>, which
        # would otherwise leak a stray "… live – latest updates" bullet into the
        # lede/deck.
        summary_html = re.sub(
            r"(?is)<(ul|ol|figure)\b.*?</\1>", " ", e.get("summary", "")
        )
        title = _TITLE_TAG.sub("", e.get("title", "(untitled)"))
        out.append(
            RawArticle(
                url=link,
                title=title,
                summary=clean_summary(strip_html(summary_html)),
                content=_content(e),
                author=e.get("author", ""),
                image_url=_extract_image(e),
                published_at=_published(e),
            )
        )
    return out
