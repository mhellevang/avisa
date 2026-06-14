"""Listing fetcher for sources without RSS/API. Renders an overview page with
Chromium and picks article links via a CSS selector.

Config (JSON on the source):
  link_selector: CSS selector for the <a> elements pointing to articles (required)
  limit:         max number of links (optional)
  exclude:       list of substrings; links that match are dropped (optional).
                 Useful for live blogs/SPAs that don't have their own article
                 pages (e.g. DN's news studio at direkte.dn.no/nyhetsstudio/#NNNN).
"""

from urllib.parse import urljoin

from .base import RawArticle
from .browser import BrowserSession


def fetch_playwright_listing(url: str, config: dict, default_limit: int = 40) -> list[RawArticle]:
    selector = config.get("link_selector")
    if not selector:
        raise ValueError("playwright source is missing 'link_selector' in config")
    limit = int(config.get("limit", default_limit))
    exclude = [s for s in config.get("exclude", []) if s]

    with BrowserSession() as bs:
        pairs = bs.links(url, selector)

    out: list[RawArticle] = []
    seen: set[str] = set()
    for href, text in pairs:
        full = urljoin(url, href)
        if full in seen or not text:
            continue
        if any(s in full for s in exclude):
            continue
        seen.add(full)
        out.append(RawArticle(url=full, title=text))
        if len(out) >= limit:
            break
    return out
