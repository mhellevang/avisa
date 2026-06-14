from typing import Optional

from sqlmodel import select

from .. import runtime_config
from ..config import settings
from ..db import get_session
from ..models import Article, Edition, EditionItem, utcnow


def _slot(rank: int) -> str:
    if rank == 0:
        return "lead"
    if rank <= 3:
        return "secondary"
    return "body"


def build_edition() -> Optional[int]:
    """Builds a new edition from the selected stories, sorted by score. Each edition
    is a snapshot — the front page always shows the latest edition."""
    with get_session() as s:
        selected = s.exec(
            select(Article)
            .where(Article.selected == True)  # noqa: E712
            .order_by(Article.score.desc())
        ).all()
        # Safety net: a paywall may have been detected after curation.
        if settings.filter_paywalled:
            selected = [a for a in selected if not a.paywalled]
        if not selected:
            print("[build] no selected stories — skipping edition")
            return None

        ed = Edition(built_at=utcnow(), title=runtime_config.paper_title())
        s.add(ed)
        s.commit()
        s.refresh(ed)

        for rank, a in enumerate(selected):
            s.add(
                EditionItem(
                    edition_id=ed.id,
                    article_id=a.id,
                    rank=rank,
                    slot=_slot(rank),
                )
            )
        s.commit()
        print(f"[build] edition {ed.id} with {len(selected)} stories")
        return ed.id
