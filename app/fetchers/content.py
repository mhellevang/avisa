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


# "Read also" / related-article widgets that papers splice into the body. In
# trafilatura's markdown they come out as a bare marker line followed by the
# related headline rendered as a heading — neither belongs in the article text.
_READ_ALSO = {
    "les også",
    "les mer",
    "se også",
    "read also",
    "read more",
    "related",
    "related stories",
    "anbefalte saker",
}


def _clean_markdown(md: str) -> str:
    """Drops 'read also' related-article blocks and collapses blank runs.
    A marker line (e.g. 'Les også') and the related headline that follows it
    (rendered as a markdown heading) are both removed."""
    lines = md.split("\n")
    out: list[str] = []
    drop_next_heading = False
    for line in lines:
        stripped = line.strip()
        bare = stripped.lstrip("#").strip().lower()
        if bare in _READ_ALSO:
            # Marker line — skip it and the related headline that follows.
            drop_next_heading = True
            continue
        if drop_next_heading:
            if not stripped:
                continue  # blank between marker and headline
            if stripped.startswith("#"):
                drop_next_heading = False
                continue  # the related headline itself
            drop_next_heading = False
        out.append(line)
    # Collapse runs of blank lines left behind by the removals.
    cleaned: list[str] = []
    for line in out:
        if not line.strip() and cleaned and not cleaned[-1].strip():
            continue
        cleaned.append(line)
    return "\n".join(cleaned).strip()


def _dedupe_blocks(md: str) -> str:
    """Drops verbatim-repeated paragraphs, keeping the first occurrence.
    Some pages (notably video/teaser pages) render the same headline + caption
    twice, so trafilatura returns it doubled — which can push thin non-articles
    past the content_min_chars guard. Comparison ignores leading '#' and
    whitespace so a heading and its repeat count as the same block."""
    blocks = re.split(r"\n\s*\n", md)
    seen: set[str] = set()
    out: list[str] = []
    for block in blocks:
        key = block.strip().lstrip("#").strip().lower()
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        out.append(block)
    return "\n\n".join(out).strip()


def _extract_text(html: str, url: str) -> Optional[str]:
    if not html:
        return None
    try:
        text = trafilatura.extract(
            html,
            url=url,
            include_comments=False,
            # Keep tables and links — the body renderer turns markdown tables
            # into <table> and [text](url) into anchors. With url= passed,
            # trafilatura resolves relative links to absolute.
            include_tables=True,
            include_links=True,
            # favor_precision drops surrounding page chrome (nav, "Fork", "Copy
            # link", "Metadata" on e.g. GitHub) that favor_recall keeps. Pages
            # that under-extract fall through to the Playwright pass.
            favor_precision=True,
            output_format="markdown",
        )
    except Exception:
        return None
    if not text:
        return None
    return _dedupe_blocks(_clean_markdown(text)) or None


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


# Bot-wall / JS-challenge interstitials (Anubis, Cloudflare, etc.). When a fetch
# lands on one of these, the "content" is the challenge page, not the article —
# so we discard it rather than store the boilerplate as the body.
_BOT_WALL = (
    "making sure you're not a bot",
    "proof-of-work scheme in the vein of hashcash",
    "anubis uses a proof-of-work",
    "checking your browser before",
    "verify you are human",
    "enable javascript and cookies to continue",
    "attention required! | cloudflare",
    "ddos protection by",
)


def _is_blocked(html: str) -> bool:
    if not html:
        return False
    low = html.lower()
    return any(m in low for m in _BOT_WALL)


def _is_paywalled(html: str) -> bool:
    if not html:
        return False
    low = html.lower()
    if re.search(r'"isaccessibleforfree"\s*:\s*(false|"false")', low):
        return True
    return any(m in low for m in _PAYWALL_TEXT)


def _result(html: str, url: str) -> dict:
    """{content, image, paywalled} from HTML. content is None if too short or if
    the page is a bot-wall/JS-challenge interstitial rather than the article."""
    text = None if _is_blocked(html) else _extract_text(html, url)
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
