"""Pipeline: ingest -> curate -> translate -> build.

Run by the scheduler in the background, and can be triggered manually from the web UI.
Each step is idempotent enough to be run again.
"""

import threading

from .. import i18n, progress
from .build import build_edition
from .content import fetch_new_content, fetch_selected_content
from .curate import curate
from .ingest import ingest
from .prune import prune
from .translate import translate, translate_pool_headlines

# A single pipeline run at a time. run_pipeline is triggered from many places
# (scheduler, the refresh/feedback/settings/configure routes, first-boot, and
# the stale-edition kick on page load). Two concurrent runs would clobber the
# shared progress state and contend on the same SQLite file. This lock makes a
# run a no-op while another is in flight; the scheduler's coalesce/max_instances
# and the stale-kick debounce reduce how often that happens, but don't cover the
# route triggers — this does.
_run_lock = threading.Lock()


def run_pipeline() -> dict:
    if not _run_lock.acquire(blocking=False):
        print("[pipeline] already running — skipping this trigger")
        return {"skipped": True}
    try:
        return _run_pipeline()
    finally:
        _run_lock.release()


def _run_pipeline() -> dict:
    N = 6  # number of steps, shown as "Step x/6"
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
        prune()

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
        # finish() reads result via the last stage; set running=False regardless
        progress.finish(locals().get("result"))
