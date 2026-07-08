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


def latest_edition_items(s) -> tuple[Edition | None, list[tuple[EditionItem, Article]]]:
    ed = s.exec(select(Edition).order_by(Edition.id.desc())).first()
    items: list[tuple[EditionItem, Article]] = []
    if ed:
        rows = s.exec(
            select(EditionItem, Article)
            .join(Article, EditionItem.article_id == Article.id)
            .where(EditionItem.edition_id == ed.id)
            .order_by(EditionItem.rank)
        ).all()
        items = [(ei, a) for ei, a in rows]
    return ed, items


def source_names(s) -> dict[int, str]:
    return {src.id: src.name for src in s.exec(select(Source)).all()}


def iso(dt):
    return dt.isoformat() if dt else None
