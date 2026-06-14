from datetime import timedelta

from sqlmodel import select

from .. import progress, runtime_config
from ..config import settings
from ..db import get_session
from ..i18n import current, lang_prompt_name
from ..llm import curate_articles
from ..models import Article, utcnow


def curate() -> int:
    """Selects front-page stories from recent articles. Resets the previous selection
    within the window and marks the newly selected ones."""
    with get_session() as s:
        cutoff = utcnow() - timedelta(hours=48)
        candidates = s.exec(
            select(Article).where(Article.fetched_at >= cutoff)
        ).all()
        if not candidates:
            print("[curate] no candidates")
            return 0

        progress.detail(current("Assessing {n} stories against the profile …", n=len(candidates)))

        # Reset the selection within the window before re-curating.
        for a in candidates:
            a.selected = False

        # Exclude stories without real body text (e.g. live-blog snippets that didn't
        # yield full text in the content phase). Without this, an empty story could end
        # up on the front page with a title and no text.
        rankable = [
            a for a in candidates
            if a.content and len(a.content) >= settings.content_min_chars
        ]
        thin = len(candidates) - len(rankable)
        if thin:
            print(f"[curate] skipped {thin} without body text")

        # Exclude stories behind a paywall (None = unknown → treated as open).
        if settings.filter_paywalled:
            before = len(rankable)
            rankable = [a for a in rankable if not a.paywalled]
            skipped = before - len(rankable)
            if skipped:
                print(f"[curate] skipped {skipped} behind paywall")

        ranked = curate_articles(
            rankable,
            runtime_config.preferences(),
            runtime_config.front_page_size(),
            target=lang_prompt_name(runtime_config.paper_lang()),
        )
        by_id = {a.id: a for a in candidates}

        chosen = 0
        for r in ranked:
            a = by_id.get(r.get("id"))
            if not a:
                continue
            a.selected = True
            try:
                a.score = float(r.get("score", 0.5))
            except (TypeError, ValueError):
                a.score = 0.5
            a.section = r.get("section") or a.section
            a.curate_reason = r.get("reason", "")
            chosen += 1

        s.commit()
    print(f"[curate] {chosen} stories selected")
    return chosen
