"""Live fremdrift for pipeline-kjøringer. In-memory (appen kjører som én
prosess), trådsikker. Frontend poller /status og viser hva som skjer — vår
erstatning for live-narrasjonen openpaper får gratis inne i Claude Code."""

import threading
import time

_lock = threading.Lock()
_state: dict = {
    "running": False,
    "stage": "idle",
    "message": "Klar.",
    "detail": "",
    "step": 0,
    "steps": 0,
    "started": 0.0,
    "last_duration": None,  # sekunder forrige kjør tok
    "last_finished": None,  # epoch
    "result": None,
}


def begin() -> None:
    from . import i18n  # lazy: unngår import-syklus

    with _lock:
        _state.update(
            running=True,
            stage="start",
            message=i18n.current("Starting …"),
            detail="",
            step=0,
            steps=0,
            started=time.time(),
            result=None,
        )


def stage(name: str, message: str, step: int | None = None, steps: int | None = None) -> None:
    with _lock:
        _state.update(stage=name, message=message, detail="")
        if step is not None:
            _state["step"] = step
        if steps is not None:
            _state["steps"] = steps


def detail(text: str) -> None:
    with _lock:
        _state["detail"] = text


def finish(result: dict | None) -> None:
    with _lock:
        dur = time.time() - _state["started"] if _state["started"] else None
        _state.update(
            running=False,
            stage="done",
            message="Ferdig.",
            detail="",
            last_duration=round(dur, 1) if dur else None,
            last_finished=time.time(),
            result=result,
        )


def snapshot() -> dict:
    with _lock:
        s = dict(_state)
    s["elapsed"] = round(time.time() - s["started"], 1) if s["running"] and s["started"] else 0
    return s
