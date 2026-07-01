from datetime import timedelta

from sqlmodel import select

from .. import progress, runtime_config
from ..config import settings
from ..db import get_session
from ..i18n import current, lang_prompt_name
from ..llm import curate_articles
from ..models import Article, Source, utcnow


def curate() -> int:
    """Selects front-page stories from recent articles. Resets the previous selection
    and marks the newly selected ones. If the LLM is unavailable, the previous
    selection is kept untouched (better a slightly stale front page than a raw
    latest-n dump that also triggers translation costs)."""
    with get_session() as s:
        cutoff = utcnow() - timedelta(hours=48)
        candidates = s.exec(
            select(Article).where(Article.fetched_at >= cutoff)
        ).all()
        if not candidates:
            print("[curate] no candidates")
            return 0

        progress.detail(current("Assessing {n} stories against the profile …", n=len(candidates)))

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

        sources = s.exec(select(Source)).all()
        source_names = {src.id: src.name for src in sources}
        source_langs = {src.id: (src.lang or "") for src in sources}
        # Sources we leave untranslated (skip list) should keep their editorial
        # bits (reason/deck) in the story's own language, so the whole card reads
        # in one language instead of a foreign-language subtitle on top.
        plang = runtime_config.paper_lang()
        keep_in_lang = {
            a.id: lang_prompt_name(sl)
            for a in rankable
            if (sl := (source_langs.get(a.source_id) or "").strip().lower())
            and sl != plang
            and not runtime_config.should_translate(sl)
        }
        ranked = curate_articles(
            rankable,
            runtime_config.preferences(),
            runtime_config.front_page_size(),
            target=lang_prompt_name(plang),
            source_names=source_names,
            today=utcnow().date().isoformat(),
            keep_in_lang=keep_in_lang,
        )
        if ranked is None:
            # Transient LLM failure (network, rate limit, garbled JSON): keep
            # the previous selection instead of replacing it with a fallback.
            kept = len(s.exec(select(Article).where(Article.selected == True)).all())  # noqa: E712
            print(f"[curate] LLM unavailable — keeping the previous selection ({kept} stories)")
            return kept

        # Reset ALL previously selected articles, not just the ones inside the
        # candidate window — a story that ages past the window while selected
        # would otherwise never be reset and stay in every future edition.
        for a in s.exec(select(Article).where(Article.selected == True)).all():  # noqa: E712
            a.selected = False

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
            a.deck = r.get("deck", "") or ""
            chosen += 1

        s.commit()
    print(f"[curate] {chosen} stories selected")
    return chosen
