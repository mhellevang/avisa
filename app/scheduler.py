from apscheduler.schedulers.background import BackgroundScheduler

from . import runtime_config
from .pipeline import run_pipeline

_scheduler: BackgroundScheduler | None = None


def start_scheduler() -> BackgroundScheduler:
    """Starter bakgrunns-pollingen. max_instances=1 hindrer overlapp hvis et
    kjør tar lengre tid enn intervallet."""
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
    print(f"[scheduler] poller hvert {minutes}. minutt")
    return sched


def reschedule(minutes: int) -> None:
    """Endrer poll-intervallet i drift (kalt fra innstillingssiden)."""
    if _scheduler:
        _scheduler.reschedule_job("poll", trigger="interval", minutes=minutes)
        print(f"[scheduler] nytt intervall: {minutes}. minutt")


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None
