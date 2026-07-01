"""Source CRUD: smart discovery, catalogue add, manual add, and per-source
language/enable/delete."""

import json
from urllib.parse import quote

from fastapi import APIRouter, Form
from fastapi.responses import RedirectResponse
from sqlmodel import select

from .. import catalog, i18n, runtime_config
from ..db import get_session
from ..fetchers import discover
from ..models import Source

router = APIRouter()

_VALID_KINDS = ("rss", "api", "playwright")


def add_source(prop: dict) -> None:
    """Single writer for inserting a source from a discovery proposal / catalog
    entry. Keeping this in one place stops the insert sites from drifting apart
    (one of them used to skip `lang`, which mistagged sources as English)."""
    with get_session() as s:
        s.add(
            Source(
                name=prop["name"],
                kind=prop["kind"],
                url=prop["url"],
                section=prop.get("section") or "News",
                # Empty = unknown → translated by default. Never default to "en".
                lang=prop.get("lang") or "",
                enabled=True,
                config=prop.get("config"),
            )
        )
        s.commit()


@router.post("/sources/discover")
def source_discover(query: str = Form(...)):
    """Smart source setup: figure out a bare URL/domain and add it."""
    prop = discover.propose(query)
    if prop.get("ok"):
        add_source(prop)
        msg = i18n.current(
            "✓ Added «{name}» — {kind} in {section}, {n} stories found.",
            name=prop["name"], kind=prop["kind"].upper(),
            section=prop["section"], n=prop["entries"],
        )
    else:
        msg = "⚠ " + prop.get("reason", i18n.current("Couldn't figure out the source."))
    return RedirectResponse(url=f"/settings?msg={quote(msg)}", status_code=303)


@router.post("/sources/catalog-add")
def source_catalog_add(
    region: str = Form(""),
    urls: list[str] = Form(default=[]),
):
    """Add the checked catalogue sources, skipping any already present."""
    region = (region or "").strip().lower()
    if region:
        runtime_config.set_value("home_region", region)
    by_url = {c["url"]: c for c in catalog.SOURCES}
    added = 0
    with get_session() as s:
        have = {(src.url or "").strip() for src in s.exec(select(Source)).all()}
    for u in urls:
        c = by_url.get(u)
        if not c or c["url"] in have:
            continue
        add_source(c)
        have.add(c["url"])
        added += 1
    msg = i18n.current("Added {n} sources.", n=added) if added else i18n.current("No new sources added.")
    return RedirectResponse(url=f"/settings?msg={quote(msg)}#sources", status_code=303)


@router.post("/sources/add")
def source_add(
    name: str = Form(...),
    kind: str = Form(...),
    url: str = Form(...),
    section: str = Form("News"),
    lang: str = Form(""),
    config: str = Form(""),
):
    kind = kind.strip().lower()
    if kind not in _VALID_KINDS:
        msg = "⚠ " + i18n.current("Unknown source type «{kind}».", kind=kind)
        return RedirectResponse(url=f"/settings?msg={quote(msg)}#sources", status_code=303)
    cfg = None
    config = config.strip()
    if config:
        try:
            json.loads(config)  # validate
            cfg = config
        except json.JSONDecodeError:
            # Tell the user instead of silently adding a source that fetches
            # nothing because its selector config was dropped.
            msg = "⚠ " + i18n.current("Invalid JSON in config — the source was not added.")
            return RedirectResponse(url=f"/settings?msg={quote(msg)}#sources", status_code=303)
    add_source(
        {
            "name": name.strip(),
            "kind": kind,
            "url": url.strip(),
            "section": section.strip() or "News",
            "lang": lang.strip().lower(),
            "config": cfg,
        }
    )
    return RedirectResponse(url="/settings", status_code=303)


@router.post("/sources/{source_id}/lang")
def source_set_lang(source_id: int, lang: str = Form("")):
    with get_session() as s:
        src = s.get(Source, source_id)
        if src:
            # Empty is a valid choice: "unknown" → translated by default.
            src.lang = lang.strip().lower()
            s.commit()
    return RedirectResponse(url="/settings", status_code=303)


@router.post("/sources/{source_id}/toggle")
def source_toggle(source_id: int):
    with get_session() as s:
        src = s.get(Source, source_id)
        if src:
            src.enabled = not src.enabled
            s.commit()
    return RedirectResponse(url="/settings", status_code=303)


@router.post("/sources/{source_id}/delete")
def source_delete(source_id: int):
    with get_session() as s:
        src = s.get(Source, source_id)
        if src:
            s.delete(src)
            s.commit()
    return RedirectResponse(url="/settings", status_code=303)
