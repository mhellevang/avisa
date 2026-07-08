import re
from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser
from typing import Optional

# Browser-like User-Agent shared by every HTTP fetcher — some sites block
# library defaults (python-httpx/feedparser UAs).
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
)


@dataclass
class RawArticle:
    url: str
    title: str
    summary: str = ""
    content: str = ""
    author: str = ""
    image_url: str = ""
    published_at: Optional[datetime] = None


# Block-level tags: their boundaries are paragraph/line breaks. Without a
# separator the text of adjacent blocks fuses ("…first paragraph.Second
# paragraph…" — a run-on summary, or a whole RSS content:encoded body collapsed
# into one wall-of-text paragraph). Emit a newline at each boundary so paragraph
# structure survives.
_BLOCK_TAGS = {
    "p", "br", "div", "li", "ul", "ol", "tr", "table", "section", "article",
    "figure", "figcaption", "blockquote", "header", "footer", "aside", "hr",
    "h1", "h2", "h3", "h4", "h5", "h6",
}


class _Stripper(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_startendtag(self, tag: str, attrs) -> None:
        if tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def text(self) -> str:
        return "".join(self.parts)


def strip_html(raw: str) -> str:
    """Removes HTML tags. Intra-line whitespace is collapsed, but block-element
    boundaries are preserved as blank lines so paragraph structure survives (a
    caller that wants a single line — a lede — collapses the newlines itself)."""
    if not raw:
        return ""
    s = _Stripper()
    try:
        s.feed(raw)
        text = s.text()
    except Exception:
        text = raw
    lines = [" ".join(ln.split()) for ln in text.split("\n")]
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text
