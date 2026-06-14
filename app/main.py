import threading
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlmodel import select

from . import auth
from .db import get_session, init_db
from .models import Edition
from .pipeline import run_pipeline
from .routes import router
from .scheduler import start_scheduler, stop_scheduler
from .seed import seed_sources

# Flater som krever innlogging når ADMIN_PASSWORD er satt.
_PROTECTED = ("/settings", "/sources", "/configure", "/feedback", "/refresh")

STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    seed_sources()

    # Kjør et førstegangs-pipeline i bakgrunnen hvis vi ikke har noen utgave
    # ennå, så forsiden ikke er tom ved første oppstart.
    with get_session() as s:
        has_edition = s.exec(select(Edition)).first() is not None
    if not has_edition:
        threading.Thread(target=run_pipeline, daemon=True).start()

    start_scheduler()
    yield
    stop_scheduler()


app = FastAPI(title="Avisa", lifespan=lifespan)


@app.middleware("http")
async def guard_admin(request, call_next):
    """Krever innlogging på admin-flatene når auth er på. Lesing er alltid åpen."""
    path = request.url.path
    if any(path == p or path.startswith(p + "/") for p in _PROTECTED):
        if not auth.is_authed(request):
            return RedirectResponse(
                url=f"/login?next={quote(path)}", status_code=303
            )
    return await call_next(request)


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
app.include_router(router)
