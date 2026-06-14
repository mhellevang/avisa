from apscheduler.schedulers.background import BackgroundScheduler

from . import runtime_config
from .pipeline import run_pipeline

_scheduler: BackgroundScheduler | None = None


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
