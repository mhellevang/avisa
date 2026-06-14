"""Pipeline: ingest -> curate -> translate -> build.

Kjøres av scheduleren i bakgrunnen, og kan trigges manuelt fra web-UI.
Hvert steg er idempotent nok til å kjøres om igjen.
"""

from .. import i18n, progress
from .build import build_edition
from .content import fetch_new_content, fetch_selected_content
from .curate import curate
from .ingest import ingest
from .translate import translate, translate_pool_headlines


def run_pipeline() -> dict:
    N = 6  # antall steg, vist som "Steg x/6"
    progress.begin()
    try:
        progress.stage("ingest", i18n.current("Fetching stories from the sources …"), 1, N)
        new = ingest()

        progress.stage("content", i18n.current("Fetching full text for new stories …"), 2, N)
        fetched = fetch_new_content()

        progress.stage("curate", i18n.current("Curating today's selection …"), 3, N)
        curated = curate()

        progress.stage("content", i18n.current("Securing full text for the front-page stories …"), 4, N)
        fetched += fetch_selected_content()

        progress.stage("translate", i18n.current("Translating …"), 5, N)
        translated = translate()
        translated += translate_pool_headlines()

        progress.stage("build", i18n.current("Assembling the edition …"), 6, N)
        edition_id = build_edition()

        result = {
            "new": new,
            "content": fetched,
            "curated": curated,
            "translated": translated,
            "edition": edition_id,
        }
        print(f"[pipeline] {result}")
        return result
    finally:
        # finish() leser result via siste stage; sett uansett running=False
        progress.finish(locals().get("result"))
