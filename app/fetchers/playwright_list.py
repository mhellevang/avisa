"""Listing-fetcher for kilder uten RSS/API. Renderer en oversiktsside med
Chromium og plukker artikkel-lenker via en CSS-selector.

Config (JSON på kilden):
  link_selector: CSS-selector for <a>-elementene som peker til artikler (påkrevd)
  limit:         maks antall lenker (valgfritt)
  exclude:       liste med substrenger; lenker som matcher droppes (valgfritt).
                 Nyttig for live-blogger/SPA-er som ikke har egne artikkelsider
                 (f.eks. DNs nyhetsstudio på direkte.dn.no/nyhetsstudio/#NNNN).
"""

from urllib.parse import urljoin

from .base import RawArticle
from .browser import BrowserSession


def fetch_playwright_listing(url: str, config: dict, default_limit: int = 40) -> list[RawArticle]:
    selector = config.get("link_selector")
    if not selector:
        raise ValueError("playwright-kilde mangler 'link_selector' i config")
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
