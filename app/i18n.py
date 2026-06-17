"""Lightweight i18n for the interface. English is the source language — the
keys ARE the English strings, so a missing translation falls back to English.
`t()` is registered as a Jinja global and reads the paper's target language
(runtime_config.paper_lang) on every call.

A new UI language = add a block to CATALOG. Article content can be translated
to any language via LANG_NAMES; the UI falls back to English for languages that
aren't in CATALOG.
"""

# The source language of the keys below. Strings in this language need no
# catalog entry — t() returns the key verbatim.
SOURCE_LANG = "en"

# code -> (label shown in the picker, name used in the LLM translation prompt).
LANG_NAMES: dict[str, tuple[str, str]] = {
    "en": ("English", "English"),
    "no": ("Norsk", "Norwegian (bokmål)"),
    "sv": ("Svenska", "Swedish"),
    "da": ("Dansk", "Danish"),
    "de": ("Deutsch", "German"),
    "fr": ("Français", "French"),
    "es": ("Español", "Spanish"),
}

# Languages we have localized the interface to (English is the source, always
# available without a catalog entry).
UI_LANGS = ("en", "no")

# Translations per language. Key = the English string used in templates/code.
CATALOG: dict[str, dict[str, str]] = {
    "no": {
        # base / shared
        "a personal newspaper · built in the background, always ready":
            "en personlig avis · bygget i bakgrunnen, alltid klar",
        # masthead / front page
        "Log in": "Logg inn",
        "Log out": "Logg ut",
        "⚙ Settings": "⚙ Innstillinger",
        "↻ Refresh": "↻ Oppdater",
        "Fetch new content now": "Hent nytt innhold nå",
        "Updated": "Oppdatert",
        "No.": "Nr.",
        "Morning edition": "Morgenutgave",
        "Evening edition": "Kveldsutgave",
        "All worth knowing, before your coffee": "Alt verdt å vite, før kaffen",
        "Building the first edition …": "Bygger første utgave …",
        "demo mode: no LLM — raw curation, untranslated":
            "demo-modus: ingen LLM — råkuratert, uoversatt",
        "Top stories": "Toppsaker",
        "In brief": "Kort fortalt",
        "More stories": "Flere saker",
        "More stories →": "Flere saker →",
        "To the editor": "Til redaktøren",
        "Tell us what you'd like more or less of — e.g. «more climate and science, fewer opinion pieces». The profile is adjusted and the paper rebuilt.":
            "Skriv hva du vil ha mer eller mindre av — f.eks. «mer klima og vitenskap, færre meningsinnlegg». Profilen justeres og avisa bygges på nytt.",
        "Your feedback …": "Din tilbakemelding …",
        "Send": "Send",
        # front-page progress JS
        "Preparing …": "Forbereder …",
        "Building your first edition …": "Bygger din første utgave …",
        "Getting started …": "Setter i gang …",
        "This runs in the background — the page refreshes itself when the paper is ready.":
            "Dette gjøres i bakgrunnen — siden oppdaterer seg selv når avisa er klar.",
        "Step": "Steg",
        "s left": "s igjen",
        "almost done …": "snart ferdig …",
        "New edition ready": "Ny utgave klar",
        "View →": "Vis →",
        "Newer edition available — refresh when you're ready":
            "Nyere utgave tilgjengelig — oppdater når du vil",
        "Toggle dark mode": "Bytt lyst/mørkt tema",
        # article
        "min read": "min lesetid",
        "Translating the article …": "Oversetter artikkelen …",
        "Couldn't translate — showing the original.":
            "Kunne ikke oversette — viser originalteksten.",
        "Read the full story at the source:": "Les hele saken hos kilden:",
        "Translated from the original:": "Oversatt fra original:",
        "← Previous": "← Forrige",
        "Next →": "Neste →",
        # more
        "No more stories right now.": "Ingen flere saker akkurat nå.",
        # login
        "Password": "Passord",
        "⚠ Wrong password.": "⚠ Feil passord.",
        # settings
        "Settings": "Innstillinger",
        "✓ Saved.": "✓ Lagret.",
        "💬 Talk to the configurator": "💬 Snakk med konfiguratoren",
        "Clear conversation": "Tøm samtale",
        "Describe in your own words what you want to change — or ask about the setup. It replies and acts.":
            "Skriv med egne ord hva du vil endre — eller spør om oppsettet. Den svarer og utfører.",
        "Requires an LLM. Set": "Krever en LLM. Sett",
        "or run locally with a logged-in": "eller kjør lokalt med en innlogget",
        "session, then you can control everything with free text here.":
            "-session, så kan du styre alt med fritekst her.",
        "Paper & profile": "Avis & profil",
        "LLM:": "LLM:",
        "Title": "Tittel",
        "Editorial profile (drives curation)": "Redaksjonell profil (styrer kurateringen)",
        "Front-page stories": "Saker på forsiden",
        "Poll interval (minutes)": "Poll-intervall (minutter)",
        "Paper language": "Avisas språk",
        "Content is translated to this language, and the interface is shown in it (where localized).":
            "Innhold oversettes til dette språket, og grensesnittet vises på det (om lokalisert).",
        "Leave untouched (don't translate these source languages)":
            "La stå urørt (ikke oversett disse kildespråkene)",
        "Stories from these source languages are shown in the original even if they differ from the paper language.":
            "Saker fra disse kildespråkene vises på originalspråk selv om de avviker fra avisas språk.",
        "Save": "Lagre",
        "Sources": "Kilder",
        "Name": "Navn",
        "Type": "Type",
        "Section": "Seksjon",
        "Language": "Språk",
        "Status": "Status",
        "On": "På",
        "Off": "Av",
        "Turn off": "Skru av",
        "Turn on": "Skru på",
        "Delete": "Slett",
        "No sources yet.": "Ingen kilder ennå.",
        "Add a source — just paste a URL or a name":
            "Legg til kilde — bare lim inn en URL eller et navn",
        "Figure it out": "Finn ut av det",
        "Advanced: add manually": "Avansert: legg til manuelt",
        "Add": "Legg til",
        "URL": "URL",
        # backend: progress messages (pipeline)
        "Starting …": "Starter …",
        "Fetching stories from the sources …": "Henter saker fra kildene …",
        "Fetching full text for new stories …": "Henter fulltekst for nye saker …",
        "Curating today's selection …": "Kuraterer dagens utvalg …",
        "Securing full text for the front-page stories …":
            "Sikrer fulltekst på forsidesakene …",
        "Translating …": "Oversetter …",
        "Assembling the edition …": "Setter sammen utgaven …",
        "Assessing {n} stories against the profile …":
            "Vurderer {n} saker mot profilen …",
        "Fetching full text {done}/{total}": "Henter fulltekst {done}/{total}",
        "Rendering JS page {i}/{total} …": "Renderer JS-side {i}/{total} …",
        "0/{total} stories": "0/{total} saker",
        "Translating {done}/{total}": "Oversetter {done}/{total}",
        # backend: article / curation fallback
        "Article not found": "Fant ikke artikkelen",
        "Latest story (no LLM key set)": "Nyeste sak (ingen LLM-nøkkel satt)",
        "Latest story": "Nyeste sak",
        "Fallback selection": "Reserveutvalg",
        # backend: configurator chat
        "I need an LLM to understand free text. Set OPENROUTER_API_KEY, or run locally with a logged-in claude session.":
            "Jeg trenger en LLM for å forstå fritekst. Sett OPENROUTER_API_KEY, eller kjør lokalt med en innlogget claude-session.",
        "Sorry, I couldn't interpret that. Try being a bit more specific?":
            "Beklager, jeg klarte ikke å tolke den. Prøv å være litt mer konkret?",
        "Done.": "Gjort.",
        "Understood the message, but found nothing to change.":
            "Forsto meldingen, men fant ingenting å endre.",
        "Rebuilding the paper …": "Bygger avisa på nytt …",
        "added «{name}»": "la til «{name}»",
        "couldn't find a source for «{query}»": "fant ikke kilde for «{query}»",
        "couldn't find the source «{name}»": "fant ikke kilden «{name}»",
        "removed «{name}»": "fjernet «{name}»",
        "turned on «{name}»": "skrudde på «{name}»",
        "turned off «{name}»": "skrudde av «{name}»",
        "updated the profile": "oppdaterte profilen",
        "set the title to «{value}»": "satte tittel til «{value}»",
        "set the front-page size to {value}": "satte forsidestørrelse til {value}",
        "set the poll interval to {value} min": "satte poll-intervall til {value} min",
        # settings: configurator help texts and source form
        "e.g. «Add aftenposten.no, remove Hacker News, and more on climate» — or «what sources do I have?» (Enter to send, Shift+Enter for a new line)":
            "f.eks. «Legg til aftenposten.no, fjern Hacker News, og mer om klima» — eller «hvilke kilder har jeg?» (Enter for å sende, Shift+Enter for ny linje)",
        "Try: «add nrk.no» · «remove The Guardian» · «more tech, less celebrity» · «call the paper Evening Post» · «what sources do I have?»":
            "Prøv: «legg til nrk.no» · «fjern The Guardian» · «mer teknologi, mindre kjendis» · «kall avisa Kveldsposten» · «hvilke kilder har jeg?»",
        "We find the RSS feed automatically": "Vi finner RSS-feeden automatisk",
        " and let Claude name and categorize it.": " og lar Claude navngi og seksjonere den.",
        "e.g. nrk.no or https://www.aftenposten.no":
            "f.eks. nrk.no eller https://www.aftenposten.no",
        "Config (JSON, optional)": "Config (JSON, valgfritt)",
        "for playwright": "for playwright",
        # settings: profile topics
        "What do you want to read?": "Hva vil du lese?",
        "Fine-tune (optional)": "Finjuster (valgfritt)",
        "e.g. more long reads, less celebrity and sport":
            "f.eks. mer dybdesaker, mindre kjendis og sport",
        "Curation runs against:": "Kurateringen kjøres mot:",
        # settings: translation explainer
        "How translation works:": "Slik fungerer oversettelse:",
        "the paper is in": "avisa er på",
        "Everything in another language is translated, except the source languages you tick below.":
            "Alt på andre språk oversettes, unntatt kildespråkene du huker av under.",
        "Translated now:": "Oversettes nå:",
        "Shown in the original:": "Vises i original:",
        # settings: suggested sources
        "Suggested sources": "Foreslåtte kilder",
        "Region": "Region",
        "Global sources are always suggested; pick a region for local ones.":
            "Globale kilder foreslås alltid; velg en region for lokale.",
        "added": "lagt til",
        "Add selected": "Legg til valgte",
        "Added {n} sources.": "La til {n} kilder.",
        "No new sources added.": "Ingen nye kilder lagt til.",
    },
}


# Month/day names per UI language, for localized date formatting.
_MONTHS = {
    "en": ["January", "February", "March", "April", "May", "June", "July",
           "August", "September", "October", "November", "December"],
    "no": ["januar", "februar", "mars", "april", "mai", "juni",
           "juli", "august", "september", "oktober", "november", "desember"],
}
_DAYS = {
    "en": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
    "no": ["mandag", "tirsdag", "onsdag", "torsdag", "fredag", "lørdag", "søndag"],
}


def fmt_date(dt, lang: str) -> str:
    """Long date in the given UI language, e.g. 'Monday 3 June 2026' /
    'mandag 3. juni 2026'. Empty string for None."""
    if not dt:
        return ""
    lang = lang if lang in _MONTHS else SOURCE_LANG
    day, month = _DAYS[lang][dt.weekday()], _MONTHS[lang][dt.month - 1]
    if lang == "no":
        return f"{day} {dt.day}. {month} {dt.year}"
    return f"{day} {dt.day} {month} {dt.year}"


def fmt_datetime(dt, lang: str) -> str:
    """Short date + time, e.g. '3 June at 14:30' / '3. juni kl. 14:30'."""
    if not dt:
        return ""
    lang = lang if lang in _MONTHS else SOURCE_LANG
    month = _MONTHS[lang][dt.month - 1]
    clock = dt.strftime("%H:%M")
    if lang == "no":
        return f"{dt.day}. {month} kl. {clock}"
    return f"{dt.day} {month} at {clock}"


def t(key: str, lang: str = SOURCE_LANG) -> str:
    """Translate a UI string to `lang`. Fallback: English (the key itself)."""
    if lang == SOURCE_LANG:
        return key
    return CATALOG.get(lang, {}).get(key, key)


def current(key: str, **fmt) -> str:
    """Translate a UI string to the paper's current target language. Used from
    backend code (progress, configurator) where the Jinja `t` global isn't
    available. Optional formatting: current("Fetching {done}/{total}", done=3,
    total=9)."""
    from . import runtime_config  # lazy: avoid an import cycle

    s = t(key, ui_lang(runtime_config.paper_lang()))
    return s.format(**fmt) if fmt else s


def ui_lang(paper_lang: str) -> str:
    """Which UI language to use for a given target language (fallback English)."""
    return paper_lang if paper_lang in UI_LANGS else SOURCE_LANG


def lang_label(code: str) -> str:
    return LANG_NAMES.get(code, (code.upper(), code))[0]


def lang_prompt_name(code: str) -> str:
    """The language name used in the translation prompt."""
    return LANG_NAMES.get(code, (code, code))[1]
