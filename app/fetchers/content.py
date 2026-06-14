"""Fulltekst- og bilde-uttrekk. Tiered, som openpaper:

1. Statisk: httpx GET + trafilatura — raskt, ingen browser. Dekker de fleste
   nyhetssider som serverer artikkelteksten i HTML.
2. Fallback: Playwright renderer siden, så trafilatura trekker ut hovedteksten.
   Kun for sider der statisk uttrekk ga for lite (JS-tunge sider).

I tillegg hentes og:image (sosial-delingsbildet) fra HTML-en — det er som regel
høyoppløst, mye bedre enn RSS-thumbnailene.
"""

import re
from typing import Optional
from urllib.parse import urljoin

import httpx
import trafilatura

from ..config import settings

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
)


def _extract_text(html: str, url: str) -> Optional[str]:
    if not html:
        return None
    try:
        text = trafilatura.extract(
            html,
            url=url,
            include_comments=False,
            include_tables=False,
            favor_recall=True,
        )
    except Exception:
        return None
    return text or None


def _og_image(html: str, url: str) -> Optional[str]:
    """Plukker ut og:image / twitter:image fra meta-taggene."""
    if not html:
        return None
    for prop in ("og:image:secure_url", "og:image", "twitter:image", "twitter:image:src"):
        m = re.search(
            r"<meta[^>]+(?:property|name)=[\"']" + re.escape(prop) + r"[\"'][^>]*>",
            html,
            re.I,
        )
        if m:
            c = re.search(r"content=[\"']([^\"']+)[\"']", m.group(0), re.I)
            if c and c.group(1).strip():
                img = urljoin(url, c.group(1).strip())
                # Hopp over åpenbare placeholdere/logoer — da er RSS-bildet bedre.
                if re.search(r"default|placeholder|logo|fallback|share[_-]?image", img, re.I):
                    return None
                return img
    return None


# Tydelige betalingsmur-markører (norsk + engelsk). isAccessibleForFree er
# schema.org-standarden mange aviser oppgir og er det sterkeste signalet.
_PAYWALL_TEXT = (
    "kun for abonnenter",
    "bli abonnent",
    "logg inn for å lese",
    "for abonnenter",
    "abonnent for å lese",
    "subscribers only",
    "subscribe to read",
    "subscribe to continue",
    "this article is for subscribers",
    "to continue reading",
)


def _is_paywalled(html: str) -> bool:
    if not html:
        return False
    low = html.lower()
    if re.search(r'"isaccessibleforfree"\s*:\s*(false|"false")', low):
        return True
    return any(m in low for m in _PAYWALL_TEXT)


def _result(html: str, url: str) -> dict:
    """{content, image, paywalled} fra HTML. content er None hvis for kort."""
    text = _extract_text(html, url)
    if text and len(text) < settings.content_min_chars:
        text = None
    return {
        "content": text,
        "image": _og_image(html, url),
        "paywalled": _is_paywalled(html),
    }


def extract_static(url: str) -> Optional[dict]:
    """Henter og trekker ut tekst + bilde uten browser. None hvis henting feiler."""
    try:
        r = httpx.get(url, timeout=20.0, follow_redirects=True, headers={"User-Agent": _UA})
        r.raise_for_status()
    except Exception:
        return None
    return _result(r.text, url)


def extract_rendered(browser, url: str) -> Optional[dict]:
    """Trekker ut tekst + bilde fra en Playwright-rendret side."""
    html = browser.render(url)
    if not html:
        return None
    return _result(html, url)
