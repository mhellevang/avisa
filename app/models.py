from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    """Naiv UTC. Vi holder oss til naive datetimes overalt så SQLite-
    sammenligninger blir konsistente."""
    return datetime.utcnow()


class Source(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    kind: str  # "rss" | "api" | "playwright"
    url: str
    section: str = "Nyheter"
    enabled: bool = True
    # ISO-språkkode for kildens innhold (f.eks. "no", "en"). Styrer om saker
    # oversettes: språk i skip-lista (innstillinger) oversettes ikke.
    lang: str = "en"
    # Fetcher-spesifikk config som JSON-streng (f.eks. link_selector for
    # playwright-listing). None for enkle kilder.
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
    content_fetched_at: Optional[datetime] = None  # None = fulltekst ikke forsøkt ennå
    paywalled: bool = False  # oppdaget bak betalingsmur
    section: str = "Nyheter"

    # Kuratering
    score: float = 0.0
    selected: bool = False
    curate_reason: str = ""

    # Oversettelse til avisas målspråk. Kolonnenavnene har historisk "_no", men
    # innholdet er på det språket translated_lang angir (None = ikke oversatt).
    title_no: Optional[str] = None
    summary_no: Optional[str] = None
    content_no: Optional[str] = None
    translated_lang: Optional[str] = None  # språkkode cachen er oversatt til
    translated_at: Optional[datetime] = None

    # Visnings-hjelpere (norsk hvis tilgjengelig, ellers original)
    @property
    def display_title(self) -> str:
        return self.title_no or self.title

    @property
    def display_summary(self) -> str:
        return self.summary_no or self.summary


class Edition(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    built_at: datetime = Field(default_factory=utcnow)
    title: str = "Morgenavisa"


class Setting(SQLModel, table=True):
    """Nøkkel/verdi for konfig som kan endres i drift (overstyrer env-defaults).
    Brukt av web-innstillinger, CLI-veiviser og tilbakemeldingsfunksjonen."""

    key: str = Field(primary_key=True)
    value: str = ""


class EditionItem(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    edition_id: int = Field(foreign_key="edition.id", index=True)
    article_id: int = Field(foreign_key="article.id")
    rank: int = 0
    slot: str = "body"  # "lead" | "secondary" | "body"
