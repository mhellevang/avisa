"""Full-text and image extraction. Tiered, like openpaper:

1. Static: httpx GET + trafilatura — fast, no browser. Covers most news sites
   that serve the article text in HTML.
2. Fallback: Playwright renders the page, then trafilatura extracts the main
   text. Only for pages where static extraction yielded too little (JS-heavy
   pages).

In addition, og:image (the social sharing image) is fetched from the HTML — it
is usually high-resolution, much better than the RSS thumbnails.
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
    """Picks out og:image / twitter:image from the meta tags."""
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
                # Skip obvious placeholders/logos — in that case the RSS image is better.
                if re.search(r"default|placeholder|logo|fallback|share[_-]?image", img, re.I):
                    return None
                return img
    return None


# Clear paywall markers (Norwegian + English). isAccessibleForFree is the
# schema.org standard many newspapers expose and is the strongest signal.
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
    """{content, image, paywalled} from HTML. content is None if too short."""
    text = _extract_text(html, url)
    if text and len(text) < settings.content_min_chars:
        text = None
    return {
        "content": text,
        "image": _og_image(html, url),
        "paywalled": _is_paywalled(html),
    }


def extract_static(url: str) -> Optional[dict]:
    """Fetches and extracts text + image without a browser. None if the fetch fails."""
    try:
        r = httpx.get(url, timeout=20.0, follow_redirects=True, headers={"User-Agent": _UA})
        r.raise_for_status()
    except Exception:
        return None
    return _result(r.text, url)


def extract_rendered(browser, url: str) -> Optional[dict]:
    """Extracts text + image from a Playwright-rendered page."""
    html = browser.render(url)
    if not html:
        return None
    return _result(html, url)
