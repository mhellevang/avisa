"""Lett i18n for grensesnittet. Norsk streng er nøkkelen — manglende
oversettelse faller naturlig tilbake til norsk. `t()` registreres som Jinja-
global og leser avisas målspråk (runtime_config.paper_lang) ved hvert kall.

Nytt UI-språk = legg til en blokk i CATALOG. Innholds-oversetting (artikler)
kan gå til hvilket som helst språk via LANG_NAMES; UI-en faller tilbake til
norsk for språk som ikke er i CATALOG.
"""

# Kode -> (visningsnavn i velgeren, navn brukt i LLM-oversettelsesprompt).
LANG_NAMES: dict[str, tuple[str, str]] = {
    "no": ("Norsk", "norsk bokmål"),
    "en": ("English", "English"),
    "sv": ("Svenska", "Swedish"),
    "da": ("Dansk", "Danish"),
    "de": ("Deutsch", "German"),
    "fr": ("Français", "French"),
    "es": ("Español", "Spanish"),
}

# Språk vi har oversatt grensesnittet til (norsk er kildespråket, alltid med).
UI_LANGS = ("no", "en")

# Oversettelser per språk. Nøkkel = den norske strengen i malen.
CATALOG: dict[str, dict[str, str]] = {
    "en": {
        # base / felles
        "en personlig avis · bygget i bakgrunnen, alltid klar":
            "a personal newspaper · built in the background, always ready",
        # masthead / forside
        "Logg inn": "Log in",
        "Logg ut": "Log out",
        "⚙ Innstillinger": "⚙ Settings",
        "↻ Oppdater": "↻ Refresh",
        "Hent nytt innhold nå": "Fetch new content now",
        "Oppdatert": "Updated",
        "Bygger første utgave …": "Building the first edition …",
        "demo-modus: ingen LLM — råkuratert, uoversatt":
            "demo mode: no LLM — raw curation, untranslated",
        "Toppsaker": "Top stories",
        "Flere saker": "More stories",
        "Flere saker →": "More stories →",
        "Til redaktøren": "To the editor",
        "Skriv hva du vil ha mer eller mindre av — f.eks. «mer klima og vitenskap, færre meningsinnlegg». Profilen justeres og avisa bygges på nytt.":
            "Tell us what you'd like more or less of — e.g. «more climate and science, fewer opinion pieces». The profile is adjusted and the paper rebuilt.",
        "Din tilbakemelding …": "Your feedback …",
        "Send": "Send",
        # forsidens fremdrifts-JS
        "Forbereder …": "Preparing …",
        "Bygger din første utgave …": "Building your first edition …",
        "Setter i gang …": "Getting started …",
        "Dette gjøres i bakgrunnen — siden oppdaterer seg selv når avisa er klar.":
            "This runs in the background — the page refreshes itself when the paper is ready.",
        "Steg": "Step",
        "s igjen": "s left",
        "snart ferdig …": "almost done …",
        "Ny utgave klar": "New edition ready",
        "Vis →": "View →",
        # artikkel
        "min lesetid": "min read",
        "Oversetter brødteksten til norsk …": "Translating the article …",
        "Kunne ikke oversette — viser originalteksten.":
            "Couldn't translate — showing the original.",
        "Les hele saken hos kilden:": "Read the full story at the source:",
        "Oversatt fra original:": "Translated from the original:",
        "← Forrige": "← Previous",
        "Neste →": "Next →",
        "Forrige": "Previous",
        "Neste": "Next",
        # flere saker
        "Ingen flere saker akkurat nå.": "No more stories right now.",
        # innlogging
        "Passord": "Password",
        "⚠ Feil passord.": "⚠ Wrong password.",
        # innstillinger
        "Innstillinger": "Settings",
        "✓ Lagret.": "✓ Saved.",
        "💬 Snakk med konfiguratoren": "💬 Talk to the configurator",
        "Tøm samtale": "Clear conversation",
        "Skriv med egne ord hva du vil endre — eller spør om oppsettet. Den svarer og utfører.":
            "Describe in your own words what you want to change — or ask about the setup. It replies and acts.",
        "Krever en LLM. Sett":
            "Requires an LLM. Set",
        "eller kjør lokalt med en innlogget":
            "or run locally with a logged-in",
        "-session, så kan du styre alt med fritekst her.":
            "session, then you can control everything with free text here.",
        "Avis & profil": "Paper & profile",
        "LLM:": "LLM:",
        "Tittel": "Title",
        "Redaksjonell profil (styrer kurateringen)":
            "Editorial profile (drives curation)",
        "Saker på forsiden": "Front-page stories",
        "Poll-intervall (minutter)": "Poll interval (minutes)",
        "Avisas språk": "Paper language",
        "Innhold oversettes til dette språket, og grensesnittet vises på det (om lokalisert).":
            "Content is translated to this language, and the interface is shown in it (where localized).",
        "La stå urørt (ikke oversett disse kildespråkene)":
            "Leave untouched (don't translate these source languages)",
        "Saker fra disse kildespråkene vises på originalspråk selv om de avviker fra avisas språk.":
            "Stories from these source languages are shown in the original even if they differ from the paper language.",
        "Lagre": "Save",
        "Kilder": "Sources",
        "Navn": "Name",
        "Type": "Type",
        "Seksjon": "Section",
        "Språk": "Language",
        "Status": "Status",
        "På": "On",
        "Av": "Off",
        "Skru av": "Turn off",
        "Skru på": "Turn on",
        "Slett": "Delete",
        "Ingen kilder ennå.": "No sources yet.",
        "Legg til kilde — bare lim inn en URL eller et navn":
            "Add a source — just paste a URL or a name",
        "Finn ut av det": "Figure it out",
        "Avansert: legg til manuelt": "Advanced: add manually",
        "Legg til": "Add",
        "URL": "URL",
    },
}


def t(key: str, lang: str = "no") -> str:
    """Oversett en UI-streng til `lang`. Fallback: norsk (nøkkelen selv)."""
    if lang == "no":
        return key
    return CATALOG.get(lang, {}).get(key, key)


def ui_lang(paper_lang: str) -> str:
    """Hvilket UI-språk som faktisk brukes for et gitt målspråk (fallback no)."""
    return paper_lang if paper_lang in UI_LANGS else "no"


def lang_label(code: str) -> str:
    return LANG_NAMES.get(code, (code.upper(), code))[0]


def lang_prompt_name(code: str) -> str:
    """Navnet på språket brukt i oversettelses-prompten."""
    return LANG_NAMES.get(code, (code, code))[1]
