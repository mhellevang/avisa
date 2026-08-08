from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Config is loaded from environment variables / .env. See .env.example."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # LLM provider: "auto" | "codex_cli" | "anthropic" | "openrouter" |
    # "claude_cli" | "none".
    llm_provider: str = "auto"

    # Logged-in Codex CLI. Empty model uses the subscription's current default.
    codex_model: str = ""

    # Anthropic direct (cheaper than OpenRouter for the same Claude models:
    # no credit markup). https://platform.claude.com/
    anthropic_api_key: str = ""

    # OpenRouter
    openrouter_api_key: str = ""
    curate_model: str = "anthropic/claude-haiku-4.5"
    translate_model: str = "anthropic/claude-sonnet-5"
    # Title/lede pre-translation is high-volume and low-stakes (short snippets
    # for the "more stories" list) — a cheap model barely shows there, while
    # body translation stays on the better model above.
    translate_headlines_model: str = "anthropic/claude-haiku-4.5"

    # Local Claude session (claude CLI). Empty model = the CLI's default model.
    claude_model: str = ""

    # IANA timezone the paper's timestamps are displayed in. Stored times are
    # naive UTC everywhere (see models.utcnow); only the display layer converts.
    # Set to your local zone so dates/clock match your wall clock, not UTC.
    timezone: str = "Europe/Oslo"

    # Build version (git SHA) baked into the image by CI; "dev" for local runs.
    # Surfaced at /status so you can confirm which build prod is actually running.
    build_version: str = "dev"

    # Pipeline / paper
    # poll_minutes controls how often sources are checked (ingest + full text,
    # no LLM — so frequent polling is free and maximizes the candidate pool).
    # Env-only; not exposed in the settings UI. The paper itself is only
    # curated/translated/rebuilt at the edition times below (HH:MM in the
    # paper's timezone, comma-separated).
    poll_minutes: int = 30
    edition_times: str = "06:30,12:30,18:30"
    front_page_size: int = 12
    # The paper's target language (ISO code). Content is translated TO this, and
    # the interface is shown in it (if localized — otherwise it falls back to
    # English).
    paper_lang: str = "en"

    # Content fetching (full text)
    content_fetch_limit: int = 40  # max new stories fetched for full text per run
    use_playwright: bool = True  # use the browser fallback for JS-heavy pages
    content_min_chars: int = 400  # below this the extraction counts as failed
    filter_paywalled: bool = True  # exclude stories behind a paywall
    translate_body_max_chars: int = 16000  # cap on body text sent to translation (covers long-reads; stays within the 8000-token output budget)
    translate_concurrency: int = 1  # Codex batches run sequentially
    translate_batch_chars: int = 24000  # max chars total per batch call
    translate_batch_max: int = 20  # max articles per batch call
    # Comma-separated language codes you want left UNTOUCHED even if they differ
    # from the target language (you read them fine yourself). Content already in
    # the target language is never translated regardless. Empty by default — the
    # target language alone decides what gets translated.
    translate_skip_langs: str = ""
    translate_headlines_limit: int = 80  # max fresh stories that get title/lede pre-translated
    paper_title: str = "The Morning Paper"
    preferences: str = (
        "General news, technology, climate and science. Weight on analysis and "
        "background over celebrity, sports and pure opinion pieces."
    )

    # DB
    database_url: str = "sqlite:///./avisa.db"
    # Days to keep old editions and unreferenced articles. Without pruning the
    # DB grows without bound (every pipeline run adds an edition). 0 = keep
    # everything.
    retention_days: int = 30

    # Login for the admin surfaces (/settings, sources, feedback, refresh).
    # Empty = no login (everything open — fine locally / behind a VPN).
    admin_password: str = ""
    # Secret for cookie signing (falls back to admin_password).
    session_secret: str = ""
    # Mark the login cookie Secure (HTTPS-only). Leave false for plain-HTTP
    # localhost; set true when served over TLS (e.g. behind a Cloudflare Tunnel),
    # so the browser never sends the admin cookie over cleartext HTTP.
    cookie_secure: bool = False


settings = Settings()
