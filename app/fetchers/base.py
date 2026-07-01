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


class _Stripper(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def text(self) -> str:
        return "".join(self.parts)


def strip_html(raw: str) -> str:
    """Removes HTML tags and normalizes whitespace."""
    if not raw:
        return ""
    s = _Stripper()
    try:
        s.feed(raw)
        text = s.text()
    except Exception:
        text = raw
    return " ".join(text.split())
