import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from . import progress, runtime_config
from .config import settings
from .pipeline import run_ingest, run_pipeline

_scheduler: BackgroundScheduler | None = None

_kick_lock = threading.Lock()
_last_kick = 0.0
_last_ingest = 0.0


def _ingest_tick() -> None:
    """Ingest-only poll. Also stamps the time so the page-load kick knows the
    pool is fresh (the interval job and the kick share this counter)."""
    global _last_ingest
    _last_ingest = time.time()
    run_ingest()


def refresh_if_stale(built_at: datetime | None) -> bool:
    """Page-load kick. Editions are only built on the cron schedule
    (edition_times) — between editions a page load just tops up the candidate
    pool with an ingest-only run when the poll interval has lapsed. A full
    pipeline run is triggered only when no edition exists at all (first boot).

    Debounced so a burst of page loads doesn't enqueue several runs in the
    window before the run flips progress to 'running'."""
    global _last_kick
    if progress.snapshot()["running"]:
        return False
    if built_at is None:
        target = run_pipeline
    else:
        if time.time() - _last_ingest < settings.poll_minutes * 60:
            return False
        target = _ingest_tick
    with _kick_lock:
        now = time.time()
        if now - _last_kick < 30:
            return False
        _last_kick = now
    threading.Thread(target=target, daemon=True).start()
    return True


def _add_edition_jobs(sched: BackgroundScheduler) -> None:
    tz = ZoneInfo(settings.timezone)
    times = runtime_config.edition_times()
    for i, hhmm in enumerate(times):
        h, m = hhmm.split(":")
        sched.add_job(
            run_pipeline,
            trigger=CronTrigger(hour=int(h), minute=int(m), timezone=tz),
            id=f"edition-{i}",
            max_instances=1,
            coalesce=True,
        )
    print(f"[scheduler] editions at {', '.join(times)} ({settings.timezone})")


def start_scheduler() -> BackgroundScheduler:
    """Starts the background jobs: an ingest-only poll every poll_minutes
    (no LLM), and a full pipeline run (curate/translate/build) at each
    edition time. max_instances=1 prevents overlap if a run takes longer
    than the interval."""
    global _scheduler
    if _scheduler:
        return _scheduler

    minutes = settings.poll_minutes
    sched = BackgroundScheduler(timezone="UTC")
    sched.add_job(
        _ingest_tick,
        trigger="interval",
        minutes=minutes,
        id="poll",
        max_instances=1,
        coalesce=True,
    )
    _add_edition_jobs(sched)
    sched.start()
    _scheduler = sched
    print(f"[scheduler] polling sources every {minutes} min")
    return sched


def reschedule_editions() -> None:
    """Re-reads edition_times and replaces the edition cron jobs (called from
    the settings page)."""
    if _scheduler:
        for job in _scheduler.get_jobs():
            if job.id.startswith("edition-"):
                _scheduler.remove_job(job.id)
        _add_edition_jobs(_scheduler)


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None
