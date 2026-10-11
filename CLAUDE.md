# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Avisa is a single-tenant personal newspaper: a FastAPI app with a background pipeline that polls sources, curates a front page with an LLM, translates it to the paper's language and freezes it as an `Edition`. Web routes only read finished data from SQLite. See README.md for the user-facing overview and the `.env` table.

## Commands

```bash
./avisa dev        # foreground, uvicorn --reload (creates .venv, installs deps + Chromium on first run)
./avisa start|stop|restart|status|logs
./avisa setup      # interactive wizard (app/setup_wizard.py)

.venv/bin/python -m unittest discover tests                                   # all tests
.venv/bin/python -m unittest tests.test_codex_cli.CodexCLITest.test_reports_missing_login   # one test
```

- Python 3.13 locally, not 3.14 (SQLModel breaks). Docker uses the Playwright base image.
- No linter or formatter is configured. Tests are stdlib `unittest` with mocks, no pytest.
- Running the server costs money: startup and `GET /` can trigger pipeline runs with real LLM calls (`refresh_if_stale`), against whatever `.env` provides. Don't start it unless asked; stop it afterwards.
- `./avisa start` has no `--reload`. Python changes need `./avisa restart`; templates and static files are read per request. A server on stale code can rebuild an edition with old logic and overwrite data in the shared `avisa.db`.

## Deploy and prod data

Push to `main` → GitHub Actions builds `ghcr.io/mhellevang/avisa:latest` → Watchtower on the TrueNAS box pulls it (`docker-compose.truenas.yml`, `deploy/TRUENAS.md`). Pushing to main is effectively a prod deploy.

The local `avisa.db` is stale and not prod. Inspect real articles via the debug API in `app/routes/debug.py` (`/debug/article/{id}?full=1`, `/debug/articles`, plus `POST .../refetch|retranslate|reprocess`). Auth is the `X-Admin-Key` header only (never a query param), checked against `ADMIN_PASSWORD`. The POST routes mutate prod.

## Architecture

**Pipeline** (`app/pipeline/__init__.py`): two entry points, both behind one non-blocking `_run_lock` so concurrent triggers become no-ops.
- `run_ingest`: ingest → content. No LLM. Runs every `POLL_MINUTES` and on stale page loads.
- `run_pipeline`: ingest → content → curate → content for selected → translate (+ pool headlines) → build → prune. Runs at each `EDITION_TIMES` cron (`app/scheduler.py`), on first boot with no edition, and from admin routes (refresh, feedback, settings, configurator).
- Each step is one file under `app/pipeline/` and is meant to be re-runnable. Steps degrade silently when the LLM fails (curate keeps the old selection, translate retries next run); `llm.health()` records the failure.
- `app/progress.py` holds the live progress state served at `GET /status`.

**Fetchers** (`app/fetchers/`): `rss`, `api` (HN/Algolia) and `playwright_list` produce candidates by `Source.kind`. `content.py` extracts full text (httpx + trafilatura first, Playwright fallback) and holds most content-quality heuristics: paywall, bot walls, liveblog rejection, cleanup. `discover.py` finds feeds/selectors when a source is added by name or URL. URL-level skip rules live in `pipeline/ingest.py` (`_SKIP_URL_MARKERS`).

**LLM** (`app/llm.py`): all provider calls go through `_chat`/`_chat_ex`, which dispatch on `LLM_PROVIDER` (`codex_cli`, `anthropic`, `openrouter`, `claude_cli`, `auto`, `none`). The CLI providers shell out via `subprocess`. Hard errors put a provider on hold (`_hard_error_holdoff`). Code blocks and images are masked before translation and restored after. Long bodies are translated in chunks with one retry per chunk. Models: `CURATE_MODEL`, `TRANSLATE_MODEL` (body), `TRANSLATE_HEADLINES_MODEL` (titles/decks). Everything must keep working with no LLM: newest stories, original text.

**Lazy body work**: the article page renders first, then fetches `GET /article/{id}/body`, which does a static-only full-text fetch if none exists and translates the body if not cached. No Playwright on the request path.

**Data** (`app/models.py`, `app/db.py`):
- No migration tool. `init_db` runs `create_all` plus `_migrate`, which only adds missing columns. Renames, type changes and drops need manual handling.
- SQLite in WAL mode with `busy_timeout`, because the pipeline thread and web workers write concurrently.
- All datetimes are naive UTC (`models.utcnow`). Only the display layer converts (`i18n.to_local`).
- `Article.title_no/summary_no/content_no` hold the translation in whatever language `translated_lang` says. The `_no` suffix is historical.
- `Source.lang` defaults to `""` (unknown → translate). `runtime_config.should_translate` skips sources whose lang equals the paper language or is in the skip list. "Language X isn't translated" is usually a wrong `Source.lang` row.

**Runtime config** (`app/runtime_config.py`): `PAPER_TITLE`, `PREFERENCES`, `FRONT_PAGE_SIZE`, `EDITION_TIMES` and paper language are env defaults overridden by rows in the `Setting` table, edited from `/settings`, the wizard, the feedback box and the LLM configurator. Read through `runtime_config`, not `config.settings`, for these. Changing edition times calls `scheduler.reschedule_editions()`.

**Web** (`app/routes/`): `reader` (front page, article, login, status, feedback), `settings`, `sources`, `debug`. Admin paths are listed in `_PROTECTED` in `app/main.py` and guarded by middleware when `ADMIN_PASSWORD` is set. Guests can only open articles that are in an edition (`reader._readable`), so crawlers can't trigger paid translations. Auth is a stdlib HMAC cookie (`app/auth.py`).

**UI i18n** (`app/i18n.py`): keys are the English strings. `t()` is a Jinja global keyed on the paper language; `i18n.current()` is for Python code. Missing translations fall back to English.

`sources.yaml` only seeds the `Source` table when it is empty.
