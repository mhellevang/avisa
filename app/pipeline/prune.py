"""Retention: every pipeline run adds an edition (dozens per day at short poll
intervals) and articles accumulate forever, so without pruning the SQLite file
grows without bound and every full-table scan gets slower. Deletes editions and
articles older than settings.retention_days, keeping the newest edition (the
front page always needs one) and any article still referenced by a kept
edition."""

from datetime import timedelta

from sqlalchemy import delete
from sqlmodel import select

from ..config import settings
from ..db import get_session
from ..models import Article, Edition, EditionItem, utcnow


def prune() -> dict:
    days = settings.retention_days
    if days <= 0:
        return {"editions": 0, "articles": 0}
    cutoff = utcnow() - timedelta(days=days)
    with get_session() as s:
        latest = s.exec(select(Edition.id).order_by(Edition.id.desc())).first()
        old_editions = [
            e.id
            for e in s.exec(select(Edition).where(Edition.built_at < cutoff)).all()
            if e.id != latest
        ]
        if old_editions:
            s.execute(delete(EditionItem).where(EditionItem.edition_id.in_(old_editions)))
            s.execute(delete(Edition).where(Edition.id.in_(old_editions)))

        referenced = set(s.exec(select(EditionItem.article_id)).all())
        doomed = [
            a.id
            for a in s.exec(select(Article).where(Article.fetched_at < cutoff)).all()
            if a.id not in referenced and not a.selected
        ]
        if doomed:
            s.execute(delete(Article).where(Article.id.in_(doomed)))
        s.commit()
    if old_editions or doomed:
        print(f"[prune] removed {len(old_editions)} editions, {len(doomed)} articles (>{days}d)")
    return {"editions": len(old_editions), "articles": len(doomed)}
