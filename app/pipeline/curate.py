from datetime import timedelta

from sqlmodel import select

from .. import progress, runtime_config
from ..config import settings
from ..db import get_session
from ..llm import curate_articles
from ..models import Article, utcnow


def curate() -> int:
    """Velger ut forsidesaker fra ferske artikler. Nullstiller forrige utvalg
    i vinduet og merker de nye valgte."""
    with get_session() as s:
        cutoff = utcnow() - timedelta(hours=48)
        candidates = s.exec(
            select(Article).where(Article.fetched_at >= cutoff)
        ).all()
        if not candidates:
            print("[curate] ingen kandidater")
            return 0

        progress.detail(f"Vurderer {len(candidates)} saker mot profilen …")

        # Nullstill utvalg i vinduet før ny kuratering.
        for a in candidates:
            a.selected = False

        # Utelat saker uten reell brødtekst (f.eks. live-blogg-snutter som ikke
        # ga fulltekst i content-fasen). Uten dette kan en tom sak havne på
        # forsiden med tittel og ingen tekst.
        rankable = [
            a for a in candidates
            if a.content and len(a.content) >= settings.content_min_chars
        ]
        thin = len(candidates) - len(rankable)
        if thin:
            print(f"[curate] hoppet over {thin} uten brødtekst")

        # Utelat saker bak betalingsmur (None = ukjent → behandles som åpen).
        if settings.filter_paywalled:
            before = len(rankable)
            rankable = [a for a in rankable if not a.paywalled]
            skipped = before - len(rankable)
            if skipped:
                print(f"[curate] hoppet over {skipped} bak betalingsmur")

        ranked = curate_articles(
            rankable, runtime_config.preferences(), runtime_config.front_page_size()
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
    print(f"[curate] {chosen} saker valgt")
    return chosen
