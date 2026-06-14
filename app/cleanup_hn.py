"""One-off cleanup: repoint articles that were ingested with a Hacker News
discussion-page URL (news.ycombinator.com/item?id=…) to the real article linked
in the post body. Articles with no external link (pure discussions) are removed,
since we no longer present forum content as articles.

Run once:  python -m app.cleanup_hn
"""

import re

import httpx
from sqlmodel import delete, select

from .db import get_session
from .fetchers.api import _first_external_link
from .models import Article, EditionItem
from .pipeline.ingest import _hash

_HN_ITEM_RE = re.compile(r"news\.ycombinator\.com/item\?id=(\d+)")


def _hn_item_text(object_id: str) -> str:
    """Fetch the post body for an HN item via the Algolia items API."""
    try:
        r = httpx.get(
            f"https://hn.algolia.com/api/v1/items/{object_id}",
            timeout=20.0,
            follow_redirects=True,
        )
        r.raise_for_status()
        return r.json().get("text") or ""
    except Exception as e:
        print(f"[cleanup] kunne ikke hente HN-item {object_id}: {e}")
        return ""


def _delete_article(s, art: Article) -> None:
    s.exec(delete(EditionItem).where(EditionItem.article_id == art.id))
    s.delete(art)


def main() -> None:
    repointed = 0
    deleted = 0
    with get_session() as s:
        arts = s.exec(
            select(Article).where(Article.url.contains("news.ycombinator.com/item"))
        ).all()
        print(f"[cleanup] {len(arts)} HN-diskusjonsartikler funnet")

        for art in arts:
            m = _HN_ITEM_RE.search(art.url)
            if not m:
                continue
            link = _first_external_link(_hn_item_text(m.group(1)))

            # Ingen ekstern lenke → ren diskusjon. Fjernes.
            if not link:
                _delete_article(s, art)
                deleted += 1
                print(f"[cleanup] #{art.id} slettet (ingen artikkellenke)")
                continue

            new_hash = _hash(link)
            dup = s.exec(
                select(Article).where(
                    Article.url_hash == new_hash, Article.id != art.id
                )
            ).first()
            if dup:
                # Artikkelen finnes allerede fra en annen kilde.
                _delete_article(s, art)
                deleted += 1
                print(f"[cleanup] #{art.id} slettet (dupe av #{dup.id} → {link})")
                continue

            # Repek og nullstill for re-henting/re-oversetting (jf. routes.py).
            art.url = link
            art.url_hash = new_hash
            art.content = ""
            art.content_fetched_at = None
            art.title_no = None
            art.summary_no = None
            art.content_no = None
            art.translated_lang = None
            art.translated_at = None
            repointed += 1
            print(f"[cleanup] #{art.id} → {link}")

        s.commit()

    print(f"[cleanup] ferdig: {repointed} repekt, {deleted} slettet")


if __name__ == "__main__":
    main()
