from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser
from typing import Optional


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
    """Fjerner HTML-tagger og normaliserer whitespace."""
    if not raw:
        return ""
    s = _Stripper()
    try:
        s.feed(raw)
        text = s.text()
    except Exception:
        text = raw
    return " ".join(text.split())
