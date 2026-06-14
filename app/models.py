from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    """Naive UTC. We stick to naive datetimes everywhere so SQLite
    comparisons stay consistent."""
    return datetime.utcnow()


class Source(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    kind: str  # "rss" | "api" | "playwright"
    url: str
    section: str = "News"
    enabled: bool = True
    # ISO language code for the source's content (e.g. "no", "en"). Controls
    # whether articles are translated: languages in the skip list (settings)
    # are not translated.
    lang: str = "en"
    # Fetcher-specific config as a JSON string (e.g. link_selector for the
    # playwright listing). None for simple sources.
    config: Optional[str] = None


class Article(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    source_id: int = Field(foreign_key="source.id")

    url: str
    url_hash: str = Field(index=True)
    title: str
    summary: str = ""
    content: str = ""
    author: str = ""
    image_url: str = ""
    published_at: Optional[datetime] = None
    fetched_at: datetime = Field(default_factory=utcnow)
    content_fetched_at: Optional[datetime] = None  # None = full text not attempted yet
    paywalled: bool = False  # detected behind a paywall
    section: str = "News"

    # Curation
    score: float = 0.0
    selected: bool = False
    curate_reason: str = ""

    # Translation into the paper's target language. The column names
    # historically use "_no", but the content is in whatever language
    # translated_lang indicates (None = not translated).
    title_no: Optional[str] = None
    summary_no: Optional[str] = None
    content_no: Optional[str] = None
    translated_lang: Optional[str] = None  # language code the cache is translated to
    translated_at: Optional[datetime] = None

    # Display helpers (translated if available, otherwise the original)
    @property
    def display_title(self) -> str:
        return self.title_no or self.title

    @property
    def display_summary(self) -> str:
        return self.summary_no or self.summary


class Edition(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    built_at: datetime = Field(default_factory=utcnow)
    title: str = "Morning Edition"


class Setting(SQLModel, table=True):
    """Key/value for configuration that can be changed at runtime (overrides
    env defaults). Used by the web settings, the CLI wizard, and the feedback
    feature."""

    key: str = Field(primary_key=True)
    value: str = ""


class EditionItem(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    edition_id: int = Field(foreign_key="edition.id", index=True)
    article_id: int = Field(foreign_key="article.id")
    rank: int = 0
    slot: str = "body"  # "lead" | "secondary" | "body"
