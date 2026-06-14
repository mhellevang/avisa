from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Konfig lastes fra miljøvariabler / .env. Se .env.example."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # LLM-provider: "auto" | "openrouter" | "claude_cli" | "none"
    #   auto  -> OpenRouter hvis nøkkel er satt, ellers lokal Claude-session
    #            (claude-CLI) hvis tilgjengelig, ellers ingen LLM (fallback).
    llm_provider: str = "auto"

    # OpenRouter
    openrouter_api_key: str = ""
    curate_model: str = "anthropic/claude-haiku-4.5"
    translate_model: str = "anthropic/claude-haiku-4.5"

    # Lokal Claude-session (claude-CLI). Tom modell = CLI-ens standardmodell.
    claude_model: str = ""

    # Pipeline / avis
    poll_minutes: int = 30
    front_page_size: int = 12

    # Innholdshenting (fulltekst)
    content_fetch_limit: int = 40  # maks nye saker som fulltekst-hentes per kjør
    use_playwright: bool = True  # bruk browser-fallback for JS-tunge sider
    content_min_chars: int = 400  # under dette regnes uttrekket som mislykket
    filter_paywalled: bool = True  # utelat saker bak betalingsmur
    translate_body_max_chars: int = 8000  # kapp brødtekst som sendes til oversettelse
    translate_concurrency: int = 4  # antall oversettelses-kall som kjøres samtidig
    translate_batch_chars: int = 9000  # maks tegn samlet per batch-kall
    translate_batch_max: int = 5  # maks artikler per batch-kall
    paper_title: str = "Morgenavisa"
    preferences: str = (
        "Generelle nyheter, teknologi, klima og vitenskap. Vekt på analyse og "
        "bakgrunn fremfor kjendis, sport og rene meningsinnlegg."
    )

    # DB
    database_url: str = "sqlite:///./avisa.db"

    # Innlogging for admin-flatene (/settings, kilder, feedback, refresh).
    # Tom = ingen innlogging (alt åpent — greit lokalt / bak VPN).
    admin_password: str = ""
    # Hemmelighet for cookie-signering (faller tilbake på admin_password).
    session_secret: str = ""


settings = Settings()
