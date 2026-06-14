"""Pipeline: ingest -> curate -> translate -> build.

Kjøres av scheduleren i bakgrunnen, og kan trigges manuelt fra web-UI.
Hvert steg er idempotent nok til å kjøres om igjen.
"""

from .. import progress
from .build import build_edition
from .content import fetch_new_content, fetch_selected_content
from .curate import curate
from .ingest import ingest
from .translate import translate, translate_pool_headlines


def run_pipeline() -> dict:
    N = 6  # antall steg, vist som "Steg x/6"
    progress.begin()
    try:
        progress.stage("ingest", "Henter saker fra kildene …", 1, N)
        new = ingest()

        progress.stage("content", "Henter fulltekst for nye saker …", 2, N)
        fetched = fetch_new_content()

        progress.stage("curate", "Kuraterer dagens utvalg …", 3, N)
        curated = curate()

        progress.stage("content", "Sikrer fulltekst på forsidesakene …", 4, N)
        fetched += fetch_selected_content()

        progress.stage("translate", "Oversetter til norsk …", 5, N)
        translated = translate()
        translated += translate_pool_headlines()

        progress.stage("build", "Setter sammen utgaven …", 6, N)
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
