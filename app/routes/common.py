"""Shared bits for the route modules: the Jinja environment (with its template
globals) and small query helpers used by more than one module."""

from pathlib import Path
from urllib.parse import urlparse

from fastapi.templating import Jinja2Templates
from markupsafe import Markup, escape
from sqlmodel import select

from .. import i18n, runtime_config
from ..markdown import body_html, dropcap_html
from ..models import Article, Edition, EditionItem, Source
from ..pipeline.build import KIND_LABELS, edition_kind

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def domain(url: str) -> str:
    try:
        return urlparse(url).netloc.replace("www.", "")
    except Exception:
        return ""


def ui_lang() -> str:
    return i18n.ui_lang(runtime_config.paper_lang())


def t(key: str) -> str:
    """UI translation bound to the paper's current target language."""
    return i18n.t(key, ui_lang())


def dropcap(text: str) -> Markup:
    """Escapes plain text and wraps its first letter in a drop-cap span (used for
    the lead ingress; the body's first paragraph is capped inside body_html)."""
    return Markup(dropcap_html(str(escape(text or ""))))


# Date helpers localized to the paper's current UI language. Template names are
# kept (no_date / no_datetime) so the markup doesn't need to change.
templates.env.globals["no_date"] = lambda dt: i18n.fmt_date(dt, ui_lang())
templates.env.globals["no_datetime"] = lambda dt: i18n.fmt_datetime(dt, ui_lang())
templates.env.globals["domain"] = domain
# Callable so the title can change at runtime (settings/wizard).
templates.env.globals["paper_title"] = runtime_config.paper_title
templates.env.globals["t"] = t
templates.env.globals["ui_lang"] = ui_lang
templates.env.globals["body_html"] = body_html
templates.env.globals["dropcap"] = dropcap
# Naive-UTC timestamp -> the paper's local timezone, for the morning/evening
# label and any other place a template needs the local wall-clock hour.
templates.env.globals["to_local"] = i18n.to_local

# Per-edition masthead bits. The motto follows the edition's slant; the short
# names sit in the folio line, where the full "… edition" labels get too long.
KIND_MOTTOS = {
    "morning": "All worth knowing, before your coffee",
    "afternoon": "The day so far — and what's still moving",
    "evening": "Depth and perspective, to end the day",
}
KIND_SHORT = {"morning": "Morning", "afternoon": "Afternoon", "evening": "Evening"}
templates.env.globals["kind_label"] = lambda k: t(KIND_LABELS.get(k, "Morning edition"))
templates.env.globals["kind_short"] = lambda k: t(KIND_SHORT.get(k, "Morning"))
templates.env.globals["kind_motto"] = lambda k: t(
    KIND_MOTTOS.get(k, KIND_MOTTOS["morning"])
)

# Cache-buster for the stylesheets: the service worker serves /static/ assets
# cache-first, so a deploy would otherwise show pages styled by the previous
# CSS on the first load. The mtime changes the URL, which misses the cache.
STATIC_DIR = TEMPLATES_DIR.parent / "static"
templates.env.globals["static_v"] = str(
    int(max(p.stat().st_mtime for p in STATIC_DIR.glob("*.css")))
)


def edition_items(s, ed: Edition) -> list[tuple[EditionItem, Article]]:
    rows = s.exec(
        select(EditionItem, Article)
        .join(Article, EditionItem.article_id == Article.id)
        .where(EditionItem.edition_id == ed.id)
        .order_by(EditionItem.rank)
    ).all()
    return [(ei, a) for ei, a in rows]


def latest_edition_items(s) -> tuple[Edition | None, list[tuple[EditionItem, Article]]]:
    ed = s.exec(select(Edition).order_by(Edition.id.desc())).first()
    return ed, edition_items(s, ed) if ed else []


def edition_kind_of(ed: Edition | None) -> str:
    """Stored kind, or derived from built_at for rows predating the column."""
    if not ed:
        return ""
    return ed.kind or edition_kind(ed.built_at)


templates.env.globals["edition_kind_of"] = edition_kind_of


def editions_today(s, ref: Edition) -> list[Edition]:
    """The editions from the same local day as `ref`, oldest first — one per
    kind (a manual refresh can rebuild a slot; the latest wins)."""
    day = i18n.to_local(ref.built_at).date()
    recent = s.exec(select(Edition).order_by(Edition.id.desc()).limit(20)).all()
    by_kind: dict[str, Edition] = {}
    for e in recent:  # newest first → first seen per kind is the latest
        if i18n.to_local(e.built_at).date() == day:
            by_kind.setdefault(edition_kind_of(e), e)
    return sorted(by_kind.values(), key=lambda e: e.id)


def source_names(s) -> dict[int, str]:
    return {src.id: src.name for src in s.exec(select(Source)).all()}


def iso(dt):
    return dt.isoformat() if dt else None
