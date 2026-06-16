import threading
import time
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler

from . import progress, runtime_config
from .pipeline import run_pipeline

_scheduler: BackgroundScheduler | None = None

_kick_lock = threading.Lock()
_last_kick = 0.0


def refresh_if_stale(built_at: datetime | None) -> bool:
    """Trigger a pipeline run if the newest edition is older than the poll
    interval (or there is none yet) and nothing is already running. Lets the
    front page pull fresh content when the app has been idle, instead of
    waiting for the next poll tick. Returns True if a run was started.

    Debounced so a burst of page loads doesn't enqueue several runs in the
    window before the run flips progress to 'running'."""
    global _last_kick
    if progress.snapshot()["running"]:
        return False
    max_age = runtime_config.poll_minutes() * 60
    if built_at is not None:
        age = (datetime.utcnow() - built_at).total_seconds()
        if age < max_age:
            return False
    with _kick_lock:
        now = time.time()
        if now - _last_kick < 30:
            return False
        _last_kick = now
    threading.Thread(target=run_pipeline, daemon=True).start()
    return True


def start_scheduler() -> BackgroundScheduler:
    """Starts the background polling. max_instances=1 prevents overlap if a
    run takes longer than the interval."""
    global _scheduler
    if _scheduler:
        return _scheduler

    minutes = runtime_config.poll_minutes()
    sched = BackgroundScheduler(timezone="UTC")
    sched.add_job(
        run_pipeline,
        trigger="interval",
        minutes=minutes,
        id="poll",
        max_instances=1,
        coalesce=True,
    )
    sched.start()
    _scheduler = sched
    print(f"[scheduler] polling every {minutes} min")
    return sched


def reschedule(minutes: int) -> None:
    """Changes the poll interval at runtime (called from the settings page)."""
    if _scheduler:
        _scheduler.reschedule_job("poll", trigger="interval", minutes=minutes)
        print(f"[scheduler] new interval: {minutes} min")


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None
