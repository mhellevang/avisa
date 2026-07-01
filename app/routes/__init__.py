"""Route modules, split by responsibility:

- reader:   front page, article view, more stories, login, status, feedback
- debug:    the admin trace/reprocess surface under /debug (header-key gated)
- settings: the settings page and the configurator chat
- sources:  source CRUD (discover, catalogue, manual)

common.py holds the shared Jinja environment and query helpers; the markdown
body renderer lives in app.markdown.
"""

from fastapi import APIRouter

from . import debug, reader, settings, sources

router = APIRouter()
router.include_router(reader.router)
router.include_router(debug.router)
router.include_router(settings.router)
router.include_router(sources.router)
