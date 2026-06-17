from typing import Optional

from sqlmodel import select

from .. import runtime_config
from ..config import settings
from ..db import get_session
from ..models import Article, Edition, EditionItem, utcnow

# Halving time for the recency weight. The LLM relevance score is multiplied by
# 0.5 ** (age / RECENCY_HALF_LIFE_HOURS) so that a story this old loses half its
# weight. Keeps relevance in charge while pushing fresher news to the top.
RECENCY_HALF_LIFE_HOURS = 24.0


def _effective_score(a: Article, now) -> float:
    when = a.published_at or a.fetched_at
    age_hours = max((now - when).total_seconds() / 3600.0, 0.0)
    return a.score * (0.5 ** (age_hours / RECENCY_HALF_LIFE_HOURS))


# Editorial slot budget (openpaper's "assign editorial weight" idea). Size
# follows the LLM's own ranking (score), reconciled into a fixed budget so the
# layout is stable regardless of how the model labels things: one lead, a few
# majors (secondary), the mid body, then a compact "brief" tail.
SECONDARY_BUDGET = 3  # majors after the lead
MID_BUDGET = 6        # mid-weight body stories before the brief tail


def _slot(rank: int) -> str:
    if rank == 0:
        return "lead"
    if rank <= SECONDARY_BUDGET:
        return "secondary"
    if rank <= SECONDARY_BUDGET + MID_BUDGET:
        return "body"
    return "brief"


def build_edition() -> Optional[int]:
    """Builds a new edition from the selected stories, sorted by score. Each edition
    is a snapshot — the front page always shows the latest edition."""
    with get_session() as s:
        selected = s.exec(
            select(Article).where(Article.selected == True)  # noqa: E712
        ).all()
        # Safety net: a paywall may have been detected after curation.
        if settings.filter_paywalled:
            selected = [a for a in selected if not a.paywalled]
        if not selected:
            print("[build] no selected stories — skipping edition")
            return None

        now = utcnow()
        selected.sort(key=lambda a: _effective_score(a, now), reverse=True)

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
