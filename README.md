# Avisa

A personal newspaper that **polls news in the background**, curates and **translates
the front-page stories to Norwegian** with an LLM, and serves a finished broadsheet
on the web — so the data is always ready and nothing is processed on the fly while
you read.

Inspired by [openpaper](https://github.com/falense/openpaper/), but flipped around:
openpaper runs the whole pipeline *live* in Claude Code every time. Here the pipeline
is an always-on background service, and reading is instant.

## How it fits together

```
BACKGROUND (scheduler, every POLL_MINUTES)                  WEB (instant)
  ingest → content → curate → content* → translate → build → front page / article / more
  (RSS/   (full text   (LLM      (ensures   (LLM →      (stores
   API/    for new      against   full text  Norwegian,  edition
   PW)     stories)     profile)  on front)  incl. body) in DB)
```

- **ingest** — fetches from the sources in `sources.yaml`. Three fetcher types like openpaper: RSS (feedparser), API (httpx), Playwright (Chromium for feedless JS pages). Deduplicates on URL hash.
- **content** — fetches the **full body text** for new stories: static extraction (httpx + trafilatura) first, Playwright fallback for JS-heavy pages. Capped at the `CONTENT_FETCH_LIMIT` newest per run; front-page stories are guaranteed full text after curation.
- **curate** — the LLM ranks fresh stories against your editorial profile (`PREFERENCES`) and picks the front page. Without an LLM key: the newest stories.
- **translate** — translates **only the curated** stories to Norwegian Bokmål, including the entire body text. Cached per article (`translated_at`), so never any duplicate work.
- **build** — freezes an `Edition`. The front page always shows the latest edition.

The web routes read finished data:
- `GET /` — the front page (latest edition)
- `GET /article/{id}` — single article
- `GET /more` — more stories (pagination over the corpus, instant)
- `POST /refresh` — the "fetch new now" button: triggers the pipeline in the background

## Running locally

```bash
cd avisa
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # add OPENROUTER_API_KEY if you want curation + translation
uvicorn app.main:app --reload
```

Open http://localhost:8000. The first time, an edition is built in the background —
reload the page after a few seconds.

Feel free to use the control script instead of remembering commands:

```bash
./avisa setup     # interactive wizard: title, profile, sources
./avisa start     # start in the background
./avisa stop      # stop
./avisa logs      # follow the log
```

> **Everything works without an LLM**: the app falls back to the newest stories and
> skips translation (keeping the original text), so you see the whole flow right away.

### LLM locally — without an API key

On localhost you don't need OpenRouter. With `LLM_PROVIDER=auto` (default), the app
uses your **logged-in `claude` CLI session** for curation and translation if no
OpenRouter key is set. That gives you intelligent curation + Norwegian translation
for free via your Claude Code subscription. On a server (without `claude`),
OpenRouter is used.

## Running on a VPS (Hetzner/Fly/Railway etc.)

```bash
cp .env.example .env        # fill in the key + optionally models/profile
docker compose up -d --build
```

The SQLite database lives in a named volume (`avisa-data`), so the data survives
restarts and redeploys. Put a reverse proxy (Caddy/nginx) in front for HTTPS.

## Config (.env)

Note: `PREFERENCES`, `PAPER_TITLE`, `FRONT_PAGE_SIZE` and `POLL_MINUTES` are only
*initial values*. If you change them in the settings (web/wizard), they are saved
to the DB and override the env.

| Variable | Description |
|----------|-----------|
| `ADMIN_PASSWORD` | Protects the admin surfaces. Empty = no login (everything open). |
| `SESSION_SECRET` | Cookie signing (optional; falls back to `ADMIN_PASSWORD`). |
| `LLM_PROVIDER` | `auto` (recommended) / `openrouter` / `claude_cli` / `none`. |
| `OPENROUTER_API_KEY` | OpenRouter key. Empty + `auto` = use the local Claude session. |
| `CURATE_MODEL` / `TRANSLATE_MODEL` | OpenRouter model ID, e.g. `anthropic/claude-haiku-4.5`. |
| `CLAUDE_MODEL` | Model for the local claude CLI (empty = CLI default). Set to `haiku` for speed. |
| `TRANSLATE_CONCURRENCY` | Number of translation batch calls at once (default 4). |
| `TRANSLATE_BATCH_MAX` / `TRANSLATE_BATCH_CHARS` | How many articles / characters are packed per call. |
| `POLL_MINUTES` | Initial value: how often polling runs. |
| `FRONT_PAGE_SIZE` | Initial value: number of stories in the paper. |
| `CONTENT_FETCH_LIMIT` | Max new stories that get full text fetched per run. |
| `USE_PLAYWRIGHT` | Browser fallback for JS-heavy pages (`true`/`false`). |
| `PREFERENCES` | Initial value: editorial profile that drives curation. |
| `PAPER_TITLE` | Initial value: title of the paper. |

## Configuring sources and preferences

Four ways, all of which save to the DB (overriding the env):

- **Talk to the configurator** (requires an LLM) — the free-text field at the top of `/settings`.
  Write "add aftenposten.no and e24.no, remove Hacker News, more about climate, name
  the paper Kveldsposten". The LLM interprets it into concrete actions (add/remove/
  disable sources, adjust profile, title, size, poll interval) and carries them out,
  and rebuilds the paper if anything relevant changed.
- **Wizard** — `./avisa setup` asks about title, profile and sources.
- **Settings page** — `⚙ Settings` on the front page (`/settings`): edit the
  profile, title, front-page size and poll interval, and add / disable /
  delete sources. A changed interval takes effect immediately.
- **Feedback** — the "To the editor" field at the bottom of the front page. Write e.g.
  "more climate, fewer opinion pieces"; the LLM adjusts the profile and the paper is
  rebuilt. Without an LLM, the feedback is added as a note in the profile.

### Smart source setup

You don't need to know the feed URL, type or selector. Paste a name or a URL
("nrk.no", "aftenposten.no") into the settings or the wizard, and then:

1. the app finds the RSS feed automatically (reads `<link>` tags + common paths),
2. lets the LLM pick the best feed, name and section the source — and even guess
   a known feed URL when the site doesn't declare one (e.g. BBC), and
3. for **feedless JS pages**: renders the page with Playwright, lets the LLM suggest a
   CSS selector for the article links, and **validates it** by running it before
   the source is saved (`kind: playwright`).

Without an LLM, the deterministic RSS discovery is used on its own.

### Live progress

When the paper is being built, the front page shows what's happening in real time —
which step (fetching, full text, curating, translating, building), a count (e.g.
"Translating 5/12") and elapsed time. The first build shows a dedicated panel that
updates itself and loads the paper when it's ready. The progress is exposed at
`GET /status` (JSON).

### Reading

The front page has a **section menu** (sticky) that jumps to Domestic/Foreign/
Technology etc., **images** on top, secondary and section stories where the source
has them, and the article pages show **source, date, reading time** and
**previous/next** navigation within the edition — so you can read your way through
the paper like a newspaper.

## Login

Reading (the front page, articles, "more stories") is always open. The admin surfaces
(`/settings`, sources, feedback, the refresh button) require a login when
`ADMIN_PASSWORD` is set — then an attempt without a valid cookie is bounced to `/login`.
If `ADMIN_PASSWORD` is empty, everything is open (fine locally / behind a VPN). The
cookie is a signed HMAC token (stdlib, no extra dependencies, no server state).

## Single user (by design)

Avisa is intentionally **single-tenant**: one paper, one editorial profile, one set of
sources, one shared admin password. There is no user/account model — `Source`, `Article`,
`Edition` and `Setting` are global, and the background pipeline builds **one** edition for
the whole instance. This matches the purpose: a *personal* morning paper.

Multi-user is deliberately **not** supported. True multi-tenancy would mean accounts +
password hashing, a `user_id` on every table with per-user query scoping, and per-user
curation/translation/build — which also multiplies the (paid) LLM cost roughly linearly
per user. That's a different product than a personal newspaper.

**If you want to share it with a few people**, run **one instance per person** instead of
adding multi-tenancy — it's far simpler and fully isolated:

```bash
# Per person: own database + own settings, passed as env vars
# (pydantic-settings reads real env vars in addition to .env). A distinct
# DATABASE_URL gives a fully separate paper, sources and preferences.
OPENROUTER_API_KEY=sk-or-alice ADMIN_PASSWORD=alice-secret \
  DATABASE_URL=sqlite:///./alice.db \
  uvicorn app.main:app --port 8001
```

The cleanest isolation is **one directory (or container) per person**, each with its own
`.env` and `avisa.db`. With Docker, give each person their own compose project (`-p alice`)
and data volume. Each instance keeps its own paper, preferences and login.

## Adding sources

Edit `sources.yaml` and delete `avisa.db` (or the volume) to re-seed. Types:

- `rss` — any RSS/Atom feeds.
- `api` — JSON API (currently Hacker News / Algolia).
- `playwright` — feedless JS pages. Requires `config.link_selector` (CSS selector
  for the article links). See the example at the bottom of `sources.yaml`.

Whatever the type, the full body text is fetched per story in the content phase.

## Known limitations in this draft

- **Translation does not re-run** automatically if you add a key
  afterwards: reset `translated_at` on the relevant rows to re-translate.
- **Full-text cap**: only the `CONTENT_FETCH_LIMIT` newest new stories get full text fetched
  per run (the front-page stories are guaranteed regardless). The rest are picked up in subsequent runs.
- **Auth is a single shared password cookie** with no expiry/rotation — enough for one user behind
  a proxy/VPN, not multi-user management.
- **`/more`** paginates the whole corpus in memory; perfectly fine at this level, but should
  become SQL pagination as the database grows.
- **Locally requires Python 3.13** (not 3.14 — SQLModel incompatibility). Docker
  uses Playwright's official base image and is unaffected.

See `app/` for the code — each pipeline step is one small file under `app/pipeline/`.
