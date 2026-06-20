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


# "Recommended Stories" / related-article widgets that papers (notably Al
# Jazeera) splice mid-article. trafilatura renders the list with screen-reader
# scaffolding — a "list of N items" opener and "list X of N" markers around each
# related headline:
#
#   ## Recommended Stories
#
#   list of 4 items- list 1 of 4
#   [Headline A](url) - list 2 of 4
#   [Headline B](url) - list 3 of 4
#   [Headline C](url)
#
# The "list of N items" / "list X of N" text never occurs in genuine prose, so
# it is a reliable tell-tale. We drop the whole contiguous block plus a lone
# heading directly above it (the widget title).
_LIST_SCAFFOLD_OPEN = re.compile(r"^list of \d+ items\b", re.I)
_LIST_SCAFFOLD_ITEM = re.compile(r"\blist \d+ of \d+\b", re.I)


def _strip_related_lists(md: str) -> str:
    lines = md.split("\n")
    out: list[str] = []
    i = 0
    n = len(lines)
    while i < n:
        if _LIST_SCAFFOLD_OPEN.match(lines[i].strip()):
            # Gather the contiguous block (related items run with no blank line
            # between them; the block ends at the next blank line).
            j = i
            while j < n and lines[j].strip():
                j += 1
            block = lines[i:j]
            # Only treat it as a widget if it carries the "list X of N" markers —
            # guards against a stray prose line that merely starts "list of N…".
            if any(_LIST_SCAFFOLD_ITEM.search(ln) for ln in block):
                while out and not out[-1].strip():
                    out.pop()  # blank line between heading and block
                if out and out[-1].lstrip().startswith("#"):
                    out.pop()  # the widget title heading
                i = j
                continue
        out.append(lines[i])
        i += 1
    # Collapse runs of blank lines left behind by the removal.
    cleaned: list[str] = []
    for line in out:
        if not line.strip() and cleaned and not cleaned[-1].strip():
            continue
        cleaned.append(line)
    return "\n".join(cleaned).strip()


# Metadata header blocks: some sources (notably ScienceDaily) lead the article
# body with a definition-list of "Date: / Source: / Summary: / Share:" — page
# chrome, not article text — usually under a heading that just repeats the
# title. trafilatura keeps it because it sits inside the main content. The
# Summary item also duplicates the article's own summary and first paragraph.
_META_LABELS = {
    "date", "source", "summary", "share", "full story", "full size image",
    "story source", "journal reference", "journal references", "cite this page",
    "related topics", "related stories", "advertisement",
}


def _strip_metadata_header(md: str) -> str:
    """Drops a leading metadata definition-list (e.g. ScienceDaily's
    Date/Source/Summary/Share block) and a duplicated-title heading right above
    it. Conservative: only a bullet-list block among the first few, where the
    bullets are clearly metadata labels rather than article content."""
    blocks = re.split(r"\n\s*\n", md)
    for i, block in enumerate(blocks[:3]):
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        if not lines:
            continue
        bullets = [ln for ln in lines if ln[:2] in ("- ", "* ")]
        if len(bullets) != len(lines):
            continue  # not a pure bullet list — leave it alone
        labels = sum(
            1 for ln in bullets if ln[2:].strip().rstrip(":").strip().lower() in _META_LABELS
        )
        if labels >= 2:
            del blocks[i]
            # Drop a lone heading immediately above it — the repeated title.
            prev = blocks[i - 1].strip() if i >= 1 else ""
            if prev.startswith("#") and "\n" not in prev:
                del blocks[i - 1]
            break
    return "\n\n".join(blocks).strip()


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


# Live blogs / livetickers render as a long stream of timestamped posts, each
# its own topic ("Thursday, June 18, 10:47 a.m.", "10:50 a.m.", or 24h "10:47").
# trafilatura extracts the whole stream as one body, so a single "article" ends
# up a multi-day mishmash (e.g. the Swiss parliament summer-session ticker —
# 38k chars spanning nuclear power, VAT, Mercosur …). A genuine article has at
# most a stray timestamp; many standalone ones mean it's a live feed, not an
# article — so we discard it rather than store the stream as the body.
_LIVE_ENTRY = re.compile(
    r"^(?:[A-Za-z]+day[ ,].*?\s)?"  # optional "Monday, June 1, 2026, " prefix
    r"\d{1,2}(?:[:.]\d{2})?\s*(?:a\.m\.|p\.m\.|am|pm)$"  # 5 a.m. / 10:47 a.m.
    r"|^(?:[A-Za-z]+day[ ,].*?\s)?\d{1,2}[:.]\d{2}$",  # 24h 10:47
    re.I,
)
_LIVEBLOG_MIN_ENTRIES = 6


def _looks_like_liveblog(text: str) -> bool:
    entries = 0
    for line in text.splitlines():
        if _LIVE_ENTRY.match(line.strip()):
            entries += 1
            if entries >= _LIVEBLOG_MIN_ENTRIES:
                return True
    return False


# Thumbnail wrappers: many sites render article figures as <a href=full><img
# src=thumb></a>. trafilatura discards images nested inside a link, so unwrap
# such anchors to the bare <img> before extraction — otherwise an image-heavy
# article (e.g. a screenshot walkthrough) comes out with no images at all. When
# the anchor points at a full-resolution image (the thumbnail's target), point
# the bare <img> at it instead — the thumbnail is often too small to be useful.
_IMG_LINK_WRAP = re.compile(r"<a\b([^>]*)>\s*(<img\b[^>]*>)\s*</a>", re.I)
_HREF = re.compile(r"""href=["']([^"']+)["']""", re.I)
_IMG_SRC = re.compile(r"""\bsrc=["'][^"']*["']""", re.I)
_IMG_EXT = re.compile(r"\.(?:png|jpe?g|gif|webp|avif)(?:$|[?#])", re.I)


def _unwrap_img_links(html: str) -> str:
    def repl(m: "re.Match") -> str:
        a_attrs, img_tag = m.group(1), m.group(2)
        href_m = _HREF.search(a_attrs)
        if href_m and _IMG_EXT.search(href_m.group(1)) and _IMG_SRC.search(img_tag):
            href = href_m.group(1)
            img_tag = _IMG_SRC.sub(lambda _m: f'src="{href}"', img_tag, count=1)
        return img_tag

    return _IMG_LINK_WRAP.sub(repl, html)

# Junk images: logos, placeholders, sprites, tracking pixels, avatars, ad slots —
# never article content. Shared by the inline-image and og:image filters.
_JUNK_IMG = re.compile(
    r"default|placeholder|logo|fallback|share[_-]?image|sprite|/icons?/|avatar|/ads?/|pixel|1x1|spacer",
    re.I,
)

# An extracted markdown image: ![alt](src "optional title").
_MD_IMG = re.compile(r"!\[([^\]]*)\]\(([^)]*)\)")


def _img_key(src: str) -> str:
    """Normalizes an image URL for same-image comparison: drops the query string
    (resize/quality params) and fragment, lowercased. So a 1920×1440 hero and a
    770×513 inline crop of the same file (…getty_123.jpg?resize=…) compare equal."""
    return src.split("?")[0].split("#")[0].lower()


def _clean_images(md: str, base_url: str, hero_url: Optional[str] = None) -> str:
    """Resolves inline image srcs to absolute URLs and drops junk: empty/missing
    srcs (![]()), non-http schemes, logos/icons/tracking pixels, and SVGs.
    trafilatura leaves the src relative, so urljoin it against the article URL.
    Each image is re-emitted as a clean ![alt](src) so the body renderer (which
    only matches http(s) srcs with no spaces/parens) turns every survivor into an
    <img>; dropped images become an empty string the renderer skips.

    Also drops any inline image that is the same file as hero_url (the og:image
    shown at the top of the article) — many articles lead the body with the very
    same figure, so without this it renders twice."""
    hero_key = _img_key(hero_url) if hero_url else None

    def repl(m: "re.Match") -> str:
        alt = m.group(1)
        raw = m.group(2).strip()
        # src is the first whitespace-delimited token — drops a markdown "title".
        src = raw.split()[0].strip("<>") if raw else ""
        if not src:
            return ""
        src = urljoin(base_url, src)
        if not src.lower().startswith(("http://", "https://")):
            return ""
        if _JUNK_IMG.search(src) or src.lower().split("?")[0].endswith(".svg"):
            return ""
        if hero_key and _img_key(src) == hero_key:
            return ""  # same image as the hero/lead — don't show it twice
        return f"![{alt}]({src})"

    return _MD_IMG.sub(repl, md)


def _extract_text(html: str, url: str, hero_url: Optional[str] = None) -> Optional[str]:
    if not html:
        return None
    html = _unwrap_img_links(html)
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
            # Keep inline images (![alt](src)); _clean_images absolutizes and
            # filters them, and the body renderer turns them into <img>.
            include_images=True,
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
    cleaned = (
        _dedupe_blocks(_strip_metadata_header(_strip_related_lists(_clean_markdown(text))))
        or None
    )
    if cleaned:
        # Strip: dropping a leading duplicate-of-hero image leaves blank lines.
        cleaned = _clean_images(cleaned, url, hero_url).strip() or None
    if cleaned and _looks_like_liveblog(cleaned):
        return None
    return cleaned


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
                if _JUNK_IMG.search(img):
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
    hero = _og_image(html, url)
    text = None if _is_blocked(html) else _extract_text(html, url, hero)
    if text and len(text) < settings.content_min_chars:
        text = None
    return {
        "content": text,
        "image": hero,
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
