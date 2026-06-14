import threading
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlmodel import select

from . import auth
from .db import get_session, init_db
from .models import Edition
from .pipeline import run_pipeline
from .routes import router
from .scheduler import start_scheduler, stop_scheduler
from .seed import seed_sources

# Pages that require login when ADMIN_PASSWORD is set.
_PROTECTED = ("/settings", "/sources", "/configure", "/feedback", "/refresh")

STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    seed_sources()

    # Run a first-time pipeline in the background if we don't have an edition
    # yet, so the front page isn't empty on first startup.
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
    """Requires login on the admin pages when auth is on. Reading is always open."""
    path = request.url.path
    if any(path == p or path.startswith(p + "/") for p in _PROTECTED):
        if not auth.is_authed(request):
            return RedirectResponse(
                url=f"/login?next={quote(path)}", status_code=303
            )
    return await call_next(request)


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# PWA: the service worker must be served from the root to control the whole
# site (a /static/ scope can't), and the manifest needs its proper media type.
@app.get("/sw.js", include_in_schema=False)
def service_worker():
    return FileResponse(
        STATIC_DIR / "sw.js",
        media_type="application/javascript",
        headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"},
    )


@app.get("/manifest.webmanifest", include_in_schema=False)
def manifest():
    return FileResponse(
        STATIC_DIR / "manifest.webmanifest",
        media_type="application/manifest+json",
    )


app.include_router(router)
