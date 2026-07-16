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
from html import unescape
from typing import Optional
from urllib.parse import urljoin

import httpx
import trafilatura

from ..config import settings
from .base import USER_AGENT as _UA


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


# The related headline after a "read also" marker comes in three shapes: a
# markdown heading, a bare [headline](url) link line, or (e.g. Digi.no) a plain
# short text line — often preceded by the related story's teaser image.
_IMG_ONLY_LINE = re.compile(r"^!\[[^\]]*\]\([^)]*\)$")
_LINK_ONLY_LINE = re.compile(r"^\[[^\]]+\]\([^)]*\)$")


def _clean_markdown(md: str) -> str:
    """Drops 'read also' related-article blocks and collapses blank runs.
    A marker line (e.g. 'Les også' / 'Les også:'), any teaser image below it,
    and the related headline that follows (a heading, a bare link, or a short
    plain line) are all removed."""
    lines = md.split("\n")
    out: list[str] = []
    i, n = 0, len(lines)
    while i < n:
        stripped = lines[i].strip()
        bare = stripped.lstrip("#").strip().rstrip(":").strip().lower()
        if bare in _READ_ALSO:
            i += 1
            # Skip blanks and the related story's teaser image(s).
            while i < n and (
                not lines[i].strip() or _IMG_ONLY_LINE.match(lines[i].strip())
            ):
                i += 1
            # The related headline itself. A short line without sentence-ending
            # punctuation is a headline, not prose — genuine paragraphs after a
            # mid-article marker are long and end with punctuation.
            if i < n:
                s = lines[i].strip()
                if (
                    s.startswith("#")
                    or _LINK_ONLY_LINE.match(s)
                    or (len(s) <= 120 and not s.endswith((".", "!", "?", "…")))
                ):
                    i += 1
            continue
        out.append(lines[i])
        i += 1
    # Collapse runs of blank lines left behind by the removals.
    cleaned: list[str] = []
    for line in out:
        if not line.strip() and cleaned and not cleaned[-1].strip():
            continue
        cleaned.append(line)
    return "\n".join(cleaned).strip()


# Page-chrome line noise that survives extraction:
# - NPR's image-carousel controls leak "hide caption" / "toggle caption" tokens
#   (the latter glued to the first body paragraph) and stray '**' lines from a
#   bold photo-credit split across lines.
# - The Guardian leaves the accessibility skip-link of its newsletter signup
#   box as a bare line ("skip past newsletter promotion" → #EmailSignup-skip-link).
_CAPTION_TOKENS = re.compile(r"\*\*\s*(?:hide|toggle) caption\s*\*\*|\b(?:hide|toggle) caption\b", re.I)
_PROMO_LINE = re.compile(
    r"^\[?(?:skip past|after) (?:the )?newsletter promotion\b|emailsignup-skip-link",
    re.I,
)
# Standalone chrome lines that survive extraction as their own line: BBC's
# "Published" metadata label, Schibsted "Vis mer/mindre" show-more controls,
# NRK's poll disclaimer, and E24's "laget med kunstig intelligens" AI-summary
# disclaimer. Anchored to the WHOLE line (after any leading bullet/heading
# marker) so it never touches the same words used inside a real sentence.
_CHROME_LINE = re.compile(
    r"^[-*#>\s]*(?:"
    r"published"
    r"|vis (?:mer|mindre)"
    r"|denne avstemningen viser ikke\b.*"
    r"|.*\blaget med kunstig intelligens\b.*"
    r")\s*[.:]?\s*$",
    re.I,
)


def _strip_chrome_lines(md: str) -> str:
    out: list[str] = []
    for line in md.split("\n"):
        line = _CAPTION_TOKENS.sub("", line)
        stripped = line.strip()
        if stripped in ("**", "*"):
            continue  # orphaned bold/italic marker from a split caption block
        if _PROMO_LINE.search(stripped) or _CHROME_LINE.match(stripped):
            continue
        out.append(line)
    return "\n".join(out)


# Trailing reference/boilerplate sections (notably ScienceDaily): "Story
# Source:", "Journal Reference:", "Cite This Page:" — everything from the first
# such label line to the end is citation chrome, not article text.
_TAIL_LABELS = {
    "story source",
    "journal reference",
    "journal references",
    "cite this page",
    "related stories",
    "les flere artikler",   # Digi.no recommendation module
    "related topics",       # BBC footer
    "related internet links",  # BBC footer
}

# Trailing widget/CTA blocks whose heading text VARIES (so an exact label set
# can't catch them): everything from the marker to the end is chrome.
_TRAILING_WIDGET = re.compile(
    r"^[-*#>\s]*(?:"
    r"more about\b.*\bfrom the bbc"      # BBC "More about X from the BBC:"
    r"|follow topics and authors"         # The Verge footer CTA
    r"|find your next great read"         # New Yorker round-up promo
    r")",
    re.I,
)


def _strip_trailing_sections(md: str) -> str:
    lines = md.split("\n")
    for i, line in enumerate(lines):
        s = line.strip()
        if i > 0 and _TRAILING_WIDGET.match(s):
            return "\n".join(lines[:i]).strip()
        if len(s) > 40:
            continue
        bare = s.lstrip("#").strip().strip("*").strip().rstrip(":").strip().lower()
        if i > 0 and bare in _TAIL_LABELS:
            return "\n".join(lines[:i]).strip()
    return md


def _norm_title(s: str) -> str:
    return re.sub(r"[\W_]+", "", s).casefold()


def _strip_title_heading(md: str, title: str) -> str:
    """Drops a heading near the top that repeats the article title — the article
    page already renders the title above the body, so it shows twice. A leading
    level-1 heading is always the page's own title (the site's H1 may be edited
    away from the feed title, e.g. Aftenposten), so it goes regardless; deeper
    levels only when they match the known title. The title heading is not always
    the FIRST block — some sites (e.g. The Verge) lead with a standfirst/hook
    paragraph and put the '# headline' a block or two down — so scan the first
    few single-line blocks, not only line 0."""
    blocks = re.split(r"\n\s*\n", md)
    ntitle = _norm_title(title) if title else ""
    for idx in range(min(3, len(blocks))):
        b = blocks[idx].strip()
        if not b.startswith("#") or "\n" in b:
            continue
        heading_text = b.lstrip("#").strip()
        is_h1 = not b.startswith("##")
        if (idx == 0 and is_h1) or (ntitle and _norm_title(heading_text) == ntitle):
            del blocks[idx]
            return "\n\n".join(blocks).strip()
    return md


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
        # Only dedupe substantial blocks. A short line can legitimately repeat
        # (a recurring label, a one-word refrain, "Advertisement"), and dropping
        # its later occurrences changes the article's meaning; the duplicated
        # headline+caption this guard targets is long enough to clear the floor.
        if key and len(key) >= 40:
            if key in seen:
                continue
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
    # Tickers often render each timestamp as its own markdown heading ("## 10:47"),
    # and non-English tickers write "10:47 Uhr" (German) or "kl. 10.47" (Norwegian) —
    # all of these must count, not just the bare English forms. The DOTTED form
    # (10.47) is only a timestamp when it carries a marker (kl./Uhr/am/pm); a bare
    # "9.58" is a decimal number, not a clock time, and must NOT count (otherwise
    # an article that lists a handful of numbers is misread as a live feed and
    # silently discarded).
    r"^(?:#{1,6}\s*)?"                    # optional heading marker
    r"(?:[A-Za-z]+day[ ,].*?\s)?"         # optional "Monday, June 1, 2026, " prefix
    r"(?:"
    r"kl\.?\s*\d{1,2}[:.]\d{2}"                          # Norwegian "kl. 10.47"
    r"|\d{1,2}:\d{2}\s*(?:a\.m\.|p\.m\.|am|pm|uhr)?"     # colon form "10:47" (marker optional)
    r"|\d{1,2}[:.]\d{2}\s*(?:a\.m\.|p\.m\.|am|pm|uhr)"   # dotted form only WITH a marker
    r"|\d{1,2}\s*(?:a\.m\.|p\.m\.|am|pm)"                # hour-only "5 a.m."
    r")$",
    re.I,
)
_LIVEBLOG_MIN_ENTRIES = 6


def _looks_like_liveblog(text: str) -> bool:
    """A live feed is a long stream of standalone timestamp lines. Require both
    an absolute count AND that the timestamps are a real fraction of the body,
    so a normal article that happens to have a few standalone times isn't
    mistaken for a ticker (and thrown away)."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return False
    entries = sum(1 for ln in lines if _LIVE_ENTRY.match(ln))
    return entries >= _LIVEBLOG_MIN_ENTRIES and entries >= 0.1 * len(lines)


# Related/teaser widgets in Labrador CMS pages (The Register's 2026 relaunch,
# many Norwegian papers): "most popular" / "more from this tag" link lists whose
# items are each wrapped in their own <article> tag, while the real body is a
# plain <div class="bodytext">. trafilatura's candidate scoring then picks the
# widget subtree over the actual article and the story comes out as a list of
# unrelated headlines. Prune the widgets before extraction; the class names are
# the CMS's own component names, not generic words, so real content is safe.
_PRUNE_XPATH = [
    '//*[contains(@class, "articlesByTag")]',
    '//*[contains(@class, "articleList")]',
    # The Labrador article header holds kicker/title/standfirst/byline/share
    # buttons — all rendered separately by the article page (the standfirst is
    # the RSS summary shown as the ingress), so in the body it only duplicates.
    '//*[contains(@class, "articleHeader")]',
]


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
# Tokens are matched only as whole path/filename COMPONENTS (bounded by /, ., _
# or the string ends) — never as arbitrary substrings — so a real photo whose
# slug happens to contain a token doesn't get dropped: "google-pixel-9.jpg",
# "avatar-the-way-of-water.jpg", "blogosphere-map.png", "sprite-can.jpg" all
# survive (the token there is hyphen-joined into a larger word, not its own
# component). 'default' keeps its old rule (no leading boundary, but not
# followed by a letter or '/') so 'og-default.png' is caught while NPR's
# /dims3/default/strip/… CDN path is not.
_JUNK_IMG = re.compile(
    r"default(?![a-z/])"
    r"|(?:^|[/_.])(?:placeholder|logos?|fallback|sprites?|avatars?|spacer|icons?|ads?|1x1|pixel|spinner)(?:[._/]|$)"
    r"|share[_-]?image",
    re.I,
)

# An extracted markdown image: ![alt](src "optional title"). The alt may hold a
# nested ']' that is NOT the closing bracket (photo credits like "Name
# [Reuters]"), so only a ']' directly before '(' terminates it; the src may hold
# balanced parentheses (e.g. "/wiki/Foo_(bar)").
_MD_IMG = re.compile(
    r"!\[((?:[^\]]|\](?!\())*)\]\(([^()]*(?:\([^)]*\)[^()]*)*)\)"
)


# BBC ichef serves the same photo at many rendition prefixes
# (…/branded_news/1200/cpsprodpb/HASH/live/FILE for the social hero vs
# …/standard/864/cpsprodpb/HASH/live/FILE inline), so a plain path compare never
# matches and the hero renders twice. The 'cpsprodpb/HASH/live/FILE' tail is the
# stable asset id — key on it so hero and inline collapse to one.
_ICHEF_ASSET = re.compile(r"/(cpsprodpb/[^/]+/live/[^/]+)$")


def _img_key(src: str) -> str:
    """Normalizes an image URL for same-image comparison: drops the query string
    (resize/quality params) and fragment, lowercased. So a 1920×1440 hero and a
    770×513 inline crop of the same file (…getty_123.jpg?resize=…) compare equal."""
    s = src.split("?")[0].split("#")[0].lower()
    m = _ICHEF_ASSET.search(s)
    return m.group(1) if m else s


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


# <small> is fine-print by definition — in article bodies it's the photo
# credit inside <figcaption> (NRK: <small>Foto: X / NRK</small>). trafilatura
# glues its text onto the next paragraph with no separator ("… / NRKHan mener …"),
# so drop the element before extraction. The closing tag may wrap its '>' onto
# the next line (NRK writes '</small\n>'). The match is non-greedy so it stops
# at the first '</small>' — an unclosed tag simply won't match and can't swallow
# content, so no arbitrary length cap is needed (a cap would instead let long
# fine-print survive when it exceeds the cap).
_SMALL_TAG = re.compile(r"<small\b[^>]*>.*?</small\s*>", re.I | re.S)

# <u> carries no article meaning, and when it sits inside a link
# (<a><u>phrase</u></a>) trafilatura mis-serializes the surrounding text order
# (NPR), tearing one sentence into out-of-order fragments. It also comes out as
# '__phrase__' (double-underscore) markdown, which the body renderer would show
# literally. Unwrap the tags (keep the text) before extraction.
_U_TAG = re.compile(r"</?u\b[^>]*>", re.I)

# <figcaption> is a photo caption — usually a credit ("Foto: X", "Getty") or a
# one-line description. trafilatura glues it onto the adjacent paragraph with no
# separator (Schibsted/Aftenposten) or lifts a top-of-article video caption to
# the body's first line (BBC), so drop the element before extraction. The image
# itself is kept; only its caption chrome goes.
_FIGCAPTION = re.compile(r"<figcaption\b[^>]*>.*?</figcaption\s*>", re.I | re.S)

# Schibsted "WordExplainer" glossary tooltip (E24/Aftenposten): an inline term
# span wraps BOTH the term and a hidden <span class="_definition_…">definition</span>
# dropdown. trafilatura keeps the aria-hidden definition and glues it straight
# onto the term ("risikopremienEt påslag på …"), corrupting the sentence. Drop
# the definition dropdown (up to its close button); the term text is kept.
_SCHIBSTED_WORDDEF = re.compile(
    r'<span[^>]*\b_definition_[^>]*>.*?</button>\s*</span>', re.I | re.S
)

# Many sites lazy-load images: the real URLs live in srcset while src is a
# data:-URI placeholder (NRK). trafilatura reads src, so the article comes out
# image-less. Promote a real srcset candidate (the last = largest) to src.
_IMG_TAG = re.compile(r"<img\b[^>]*>", re.I)
_SRCSET_ATTR = re.compile(r'\bsrcset=["\']([^"\']+)["\']', re.I)
_SRC_ATTR = re.compile(r'\bsrc=["\']([^"\']*)["\']', re.I)


def _resolve_srcset(html: str) -> str:
    def repl(m: "re.Match") -> str:
        tag = m.group(0)
        src_m = _SRC_ATTR.search(tag)
        src = src_m.group(1) if src_m else ""
        if src and not src.lower().startswith("data:"):
            return tag  # already has a real src
        ss = _SRCSET_ATTR.search(tag)
        if not ss:
            return tag
        cands = [c.strip().split()[0] for c in ss.group(1).split(",") if c.strip()]
        if not cands:
            return tag
        real = cands[-1]
        if src_m:
            return tag[: src_m.start(1)] + real + tag[src_m.end(1):]
        return tag[:-1] + f' src="{real}">'

    return _IMG_TAG.sub(repl, html)


# Adjacent inline emphasis elements are serialized by trafilatura with no
# separator, so a run of <em> items collapses into one span with internal '**'
# boundaries — e.g. an NPR nominee list becomes a single mashed-together line:
#   *Abbott Elementary**The Bear**Nobody Wants This*
# Split such a whole-line italic run back into comma-separated italics, and put
# a space back between an emphasis span glued straight onto a following
# capitalized word ("**YouTube**Liza …").
_GLUED_ITALIC_LINE = re.compile(r"\*(?!\s)(\S.*?\S)\*")
# Only the double-star (bold) form: a bold caption/credit glued straight onto
# the next sentence ("**YouTube**Liza …"). A single-star rule would spuriously
# pair the '*, *' separators produced by the italic-run split above.
_EMPH_GLUED_CAP = re.compile(r"(\*\*[^*\n]+\*\*)(?=[A-ZÀ-Þ])")


def _deglue_emphasis(md: str) -> str:
    out: list[str] = []
    for line in md.split("\n"):
        s = line.strip()
        m = _GLUED_ITALIC_LINE.fullmatch(s)
        if m and "**" in m.group(1):
            parts = [p.strip() for p in m.group(1).split("**") if p.strip()]
            out.append(", ".join(f"*{p}*" for p in parts))
        else:
            out.append(line)
    md = "\n".join(out)
    md = _EMPH_GLUED_CAP.sub(r"\1 ", md)
    # A markdown link/image immediately followed by an alphanumeric — trafilatura
    # sometimes drops the space after a link, gluing the next word onto the
    # anchor text ("[… spotted by](url)suggests …"). Restore the space.
    md = re.sub(r"(\]\([^)]*\))(?=[0-9A-Za-zÀ-ÿ])", r"\1 ", md)
    return md


# ---- leading / orphan chrome that survives into the cleaned markdown -------

def _strip_leading_byline_bullet(md: str) -> str:
    """Drops a stray leading single-item bullet that is just a byline — some
    sources (Aftenposten) emit the author as '- Name' as the article's first
    line, which renders as a one-item <ul>. Only a SHORT name-like item (no
    sentence/label punctuation, ≤5 words, no link) that is NOT followed by
    another bullet (i.e. not a genuine list) is removed."""
    lines = md.split("\n")
    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i >= len(lines):
        return md
    first = lines[i].strip()
    if first[:2] not in ("- ", "* "):
        return md
    item = first[2:].strip()
    if (
        not item
        or len(item) > 50
        or len(item.split()) > 5
        or "](" in item
        or item.endswith((".", "!", "?", ":", ";"))
    ):
        return md
    j = i + 1
    while j < len(lines) and not lines[j].strip():
        j += 1
    if j < len(lines) and lines[j].strip()[:2] in ("- ", "* "):
        return md  # a real list — leave it
    return "\n".join(lines[:i] + lines[i + 1:]).strip()


# A leading "date · N min · #tag" chrome line (gracefulliberty via HN, others):
# it duplicates the byline date and the reading time and renders the tag as a
# link — page chrome, not article text.
_LEADING_META = re.compile(
    r"^(?:\d{4}-\d{2}-\d{2}|\d{1,2}\s+\w+\.?\s+\d{4})"      # a date leads
    r"(?:\s*·\s*(?:\d+\s*min(?:\s*read)?|#[\w-]+))+\s*$",   # · N min · #tag …
    re.I,
)


def _strip_leading_meta_line(md: str) -> str:
    lines = md.split("\n")
    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i >= len(lines):
        return md
    probe = re.sub(r"\[(#[\w-]+)\]\([^)]*\)", r"\1", lines[i].strip())
    if _LEADING_META.match(probe):
        return "\n".join(lines[:i] + lines[i + 1:]).strip()
    return md


def _strip_orphan_chart_labels(md: str) -> str:
    """Interactive chart components leave a caption/label line but no chart
    (E24/Aftenposten: 'Hopp i oljeprisen onsdag:'). Drop a short colon-terminated
    label ONLY when nothing real follows it — EOF, a heading, an image, or
    another bare label — so a genuine lead-in ('Slik gjorde vi det:' + prose) is
    kept."""
    blocks = re.split(r"\n\s*\n", md)

    def is_label(b: str) -> bool:
        b = b.strip()
        return 0 < len(b) <= 80 and b.endswith(":") and not b.startswith(
            ("#", "-", "*", "|", "!", ">")
        )

    out: list[str] = []
    for k, b in enumerate(blocks):
        if is_label(b):
            nxt = blocks[k + 1].strip() if k + 1 < len(blocks) else ""
            if not nxt or is_label(nxt) or nxt.startswith(("#", "!")):
                continue
        out.append(b)
    return "\n\n".join(out).strip()


_HEADING_LEVEL = re.compile(r"^(#{1,6})\s")


def _strip_empty_headings(md: str) -> str:
    """Drops an orphaned section heading — one with no content of its own,
    immediately followed by another heading of the same or shallower level, or
    by nothing (EOF). E.g. an NRK poll question heading whose interactive widget
    didn't extract, leaving '## Hvor skal du se kampen?' right before the next
    section. A DEEPER heading after it (a real subsection) means it is a parent,
    so it is kept."""
    lines = md.split("\n")
    out: list[str] = []
    i, n = 0, len(lines)
    while i < n:
        m = _HEADING_LEVEL.match(lines[i].strip())
        if m:
            j = i + 1
            while j < n and not lines[j].strip():
                j += 1
            if j >= n:
                i += 1  # trailing heading with no content — drop
                continue
            m2 = _HEADING_LEVEL.match(lines[j].strip())
            if m2 and len(m2.group(1)) <= len(m.group(1)):
                i += 1  # empty section — drop the orphan heading
                continue
        out.append(lines[i])
        i += 1
    return "\n".join(out).strip()


def _extract_text(
    html: str, url: str, hero_url: Optional[str] = None, title: str = ""
) -> Optional[str]:
    if not html:
        return None
    html = _SMALL_TAG.sub(" ", html)
    html = _U_TAG.sub("", html)
    html = _FIGCAPTION.sub(" ", html)
    html = _SCHIBSTED_WORDDEF.sub("", html)
    html = _resolve_srcset(html)
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
            prune_xpath=_PRUNE_XPATH,
            output_format="markdown",
        )
    except Exception:
        return None
    if not text:
        return None
    text = _strip_chrome_lines(text)
    text = _deglue_emphasis(text)
    cleaned = (
        _dedupe_blocks(_strip_metadata_header(_strip_related_lists(_clean_markdown(text))))
        or None
    )
    if cleaned:
        cleaned = _strip_trailing_sections(_strip_title_heading(cleaned, title)) or None
    if cleaned:
        cleaned = _strip_leading_byline_bullet(cleaned)
        cleaned = _strip_leading_meta_line(cleaned)
        cleaned = _strip_orphan_chart_labels(cleaned)
        cleaned = _strip_empty_headings(cleaned) or None
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
                # Attribute values are HTML-escaped — unescape or the query
                # string keeps literal '&amp;' and the params come out mangled.
                img = urljoin(url, unescape(c.group(1).strip()))
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
    # Le Monde's DataDome interstitial (served with HTTP 402): French bot notice
    # + the captcha vendor. On the static path this now reaches _result via
    # extract_static's status handling; on the Playwright path the challenge page
    # is already too short to pass content_min_chars.
    "votre trafic a été identifié comme automatisé",
    "trafic a été identifié comme automatisé",
    "captcha-delivery.com",
    "geo.captcha-delivery",
)


def _is_blocked(html: str) -> bool:
    if not html:
        return False
    low = html.lower()
    return any(m in low for m in _BOT_WALL)


def _is_paywalled(html: str, thin: bool = True) -> bool:
    if not html:
        return False
    low = html.lower()
    # isAccessibleForFree is the schema.org standard many newspapers expose, but
    # some fully-open sites (e.g. The Verge / Vox Media) carry
    # "isAccessibleForFree":false on articles that are actually free and extract
    # in full. So — like the text markers below — trust it only when extraction
    # came up short: if we got the whole body, it is not paywalled regardless of
    # the flag. Guards against false positives that would hide open articles.
    if not thin:
        return False
    if re.search(r'\\?"isaccessibleforfree\\?"\s*:\s*\\?"?false', low):
        return True
    # The text markers are searched in the WHOLE document (nav, footer,
    # newsletter banners, teasers for other stories), so on their own they
    # misfire on fully open pages. Only trust them when extraction actually
    # came up short — i.e. when a paywall plausibly withheld the body.
    return any(m in low for m in _PAYWALL_TEXT)


def _result(html: str, url: str, title: str = "") -> dict:
    """{content, image, paywalled} from HTML. content is None if too short or if
    the page is a bot-wall/JS-challenge interstitial rather than the article.
    title (when known) lets extraction drop a body-leading duplicate heading."""
    hero = _og_image(html, url)
    text = None if _is_blocked(html) else _extract_text(html, url, hero, title)
    if text and len(text) < settings.content_min_chars:
        text = None
    return {
        "content": text,
        "image": hero,
        "paywalled": _is_paywalled(html, thin=text is None),
    }


def extract_static(url: str, title: str = "") -> Optional[dict]:
    """Fetches and extracts text + image without a browser. None if the fetch
    fails at the transport level (retried next run). A 401/402/403 usually
    serves a paywall or bot-wall page rather than the article, so classify it
    (blocked/paywalled) instead of treating it as a transient failure that
    retries forever; other non-2xx statuses are treated as failures."""
    try:
        r = httpx.get(url, timeout=20.0, follow_redirects=True, headers={"User-Agent": _UA})
    except Exception:
        return None
    if not (200 <= r.status_code < 300 or r.status_code in (401, 402, 403)):
        return None
    return _result(r.text, url, title)


def extract_rendered(browser, url: str, title: str = "") -> Optional[dict]:
    """Extracts text + image from a Playwright-rendered page."""
    html = browser.render(url)
    if not html:
        return None
    return _result(html, url, title)
