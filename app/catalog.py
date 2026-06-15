"""Curated suggestions for onboarding: editorial topics that build the profile
text, and a catalogue of known news sources (global + regional) the user can
pick from with checkboxes instead of hunting for feed URLs.

The data is deliberately plain so the settings page can render it directly.
Source URLs here are verified RSS/Atom feeds (or the Hacker News API)."""


# Editorial topics. Each selected topic contributes its `phrase` to the
# generated profile text that drives curation.
TOPICS: list[dict] = [
    {"key": "world", "label_en": "World", "label_no": "Verden",
     "phrase": "world and international affairs"},
    {"key": "politics", "label_en": "Politics", "label_no": "Politikk",
     "phrase": "politics and government"},
    {"key": "business", "label_en": "Business & economy", "label_no": "Økonomi",
     "phrase": "business, economy and finance"},
    {"key": "tech", "label_en": "Technology", "label_no": "Teknologi",
     "phrase": "technology and software"},
    {"key": "science", "label_en": "Climate & science", "label_no": "Klima & vitenskap",
     "phrase": "climate, environment and science"},
    {"key": "culture", "label_en": "Culture", "label_no": "Kultur",
     "phrase": "culture and the arts"},
    {"key": "health", "label_en": "Health", "label_no": "Helse",
     "phrase": "health and wellbeing"},
    {"key": "sport", "label_en": "Sport", "label_no": "Sport",
     "phrase": "sport"},
]

# Default tone appended to the generated profile so curation favours depth.
PROFILE_TONE = (
    "Weight on analysis and background over celebrity gossip and pure opinion pieces."
)

# Regions, in the order shown in the picker. "global" sources are always
# suggested; the others are filtered by the selected region.
REGIONS: list[dict] = [
    {"code": "no", "label_en": "Norway", "label_no": "Norge"},
    {"code": "se", "label_en": "Sweden", "label_no": "Sverige"},
    {"code": "dk", "label_en": "Denmark", "label_no": "Danmark"},
    {"code": "uk", "label_en": "United Kingdom", "label_no": "Storbritannia"},
    {"code": "us", "label_en": "United States", "label_no": "USA"},
    {"code": "de", "label_en": "Germany", "label_no": "Tyskland"},
    {"code": "fr", "label_en": "France", "label_no": "Frankrike"},
]

# Suggested sources. region "global" = always shown.
SOURCES: list[dict] = [
    # — Global —
    {"name": "BBC World", "kind": "rss", "url": "https://feeds.bbci.co.uk/news/world/rss.xml",
     "section": "World", "lang": "en", "region": "global"},
    {"name": "The Guardian", "kind": "rss", "url": "https://www.theguardian.com/world/rss",
     "section": "World", "lang": "en", "region": "global"},
    {"name": "Al Jazeera", "kind": "rss", "url": "https://www.aljazeera.com/xml/rss/all.xml",
     "section": "World", "lang": "en", "region": "global"},
    {"name": "The Economist", "kind": "rss", "url": "https://www.economist.com/the-world-this-week/rss.xml",
     "section": "World", "lang": "en", "region": "global"},
    {"name": "NPR", "kind": "rss", "url": "https://feeds.npr.org/1001/rss.xml",
     "section": "World", "lang": "en", "region": "global"},
    {"name": "The New Yorker", "kind": "rss", "url": "https://www.newyorker.com/feed/everything",
     "section": "Culture", "lang": "en", "region": "global"},
    {"name": "Wall Street Journal — World", "kind": "rss", "url": "https://feeds.a.dj.com/rss/RSSWorldNews.xml",
     "section": "Business", "lang": "en", "region": "global"},
    {"name": "Financial Times", "kind": "rss", "url": "https://www.ft.com/rss/home",
     "section": "Business", "lang": "en", "region": "global"},
    {"name": "Ars Technica", "kind": "rss", "url": "https://feeds.arstechnica.com/arstechnica/index",
     "section": "Technology", "lang": "en", "region": "global"},
    {"name": "The Verge", "kind": "rss", "url": "https://www.theverge.com/rss/index.xml",
     "section": "Technology", "lang": "en", "region": "global"},
    {"name": "TechCrunch", "kind": "rss", "url": "https://techcrunch.com/feed/",
     "section": "Technology", "lang": "en", "region": "global"},
    {"name": "Hacker News", "kind": "api", "url": "https://hn.algolia.com/api/v1/search?tags=front_page",
     "section": "Technology", "lang": "en", "region": "global"},

    # — Norway —
    {"name": "NRK Nyheter", "kind": "rss", "url": "https://www.nrk.no/nyheter/siste.rss",
     "section": "Domestic", "lang": "no", "region": "no"},
    {"name": "Aftenposten", "kind": "rss", "url": "https://www.aftenposten.no/rss",
     "section": "Domestic", "lang": "no", "region": "no"},
    {"name": "VG", "kind": "rss", "url": "https://www.vg.no/rss/feed",
     "section": "Domestic", "lang": "no", "region": "no"},
    {"name": "E24", "kind": "rss", "url": "https://e24.no/rss",
     "section": "Business", "lang": "no", "region": "no"},
    {"name": "Dagens Næringsliv", "kind": "rss", "url": "https://www.dn.no/rss",
     "section": "Business", "lang": "no", "region": "no"},

    # — Sweden —
    {"name": "SVT Nyheter", "kind": "rss", "url": "https://www.svt.se/nyheter/rss.xml",
     "section": "Domestic", "lang": "sv", "region": "se"},
    {"name": "Sveriges Radio Ekot", "kind": "rss", "url": "https://api.sr.se/api/rss/program/83",
     "section": "Domestic", "lang": "sv", "region": "se"},

    # — Denmark —
    {"name": "DR Nyheder", "kind": "rss", "url": "https://www.dr.dk/nyheder/service/feeds/allenyheder",
     "section": "Domestic", "lang": "da", "region": "dk"},
    {"name": "Politiken", "kind": "rss", "url": "https://politiken.dk/rss/senestenyt.rss",
     "section": "Domestic", "lang": "da", "region": "dk"},

    # — United Kingdom —
    {"name": "BBC UK", "kind": "rss", "url": "https://feeds.bbci.co.uk/news/uk/rss.xml",
     "section": "Domestic", "lang": "en", "region": "uk"},

    # — United States —
    {"name": "The New York Times — World", "kind": "rss", "url": "https://rss.nytimes.com/services/xml/rss/nyt/World.xml",
     "section": "World", "lang": "en", "region": "us"},
    {"name": "The Washington Post — World", "kind": "rss", "url": "https://feeds.washingtonpost.com/rss/world",
     "section": "World", "lang": "en", "region": "us"},
    {"name": "The Guardian US", "kind": "rss", "url": "https://www.theguardian.com/us-news/rss",
     "section": "Domestic", "lang": "en", "region": "us"},

    # — Germany —
    {"name": "Tagesschau", "kind": "rss", "url": "https://www.tagesschau.de/index~rss2.xml",
     "section": "Domestic", "lang": "de", "region": "de"},
    {"name": "Der Spiegel — International", "kind": "rss", "url": "https://www.spiegel.de/international/index.rss",
     "section": "World", "lang": "en", "region": "de"},

    # — France —
    {"name": "Le Monde", "kind": "rss", "url": "https://www.lemonde.fr/rss/une.xml",
     "section": "Domestic", "lang": "fr", "region": "fr"},
    {"name": "France 24", "kind": "rss", "url": "https://www.france24.com/en/rss",
     "section": "World", "lang": "en", "region": "fr"},
]


def topic_phrases(keys: list[str]) -> list[str]:
    by_key = {t["key"]: t for t in TOPICS}
    return [by_key[k]["phrase"] for k in keys if k in by_key]


def build_preferences(topic_keys: list[str], extra: str = "") -> str:
    """Compose the profile text curation runs against, from chosen topics plus
    optional free-text refinement."""
    phrases = topic_phrases(topic_keys)
    parts: list[str] = []
    if phrases:
        if len(phrases) == 1:
            joined = phrases[0]
        else:
            joined = ", ".join(phrases[:-1]) + " and " + phrases[-1]
        parts.append(f"News about {joined}.")
        parts.append(PROFILE_TONE)
    extra = (extra or "").strip()
    if extra:
        parts.append(extra)
    return " ".join(parts).strip()


def suggested_sources(region: str) -> list[dict]:
    region = (region or "").strip().lower()
    return [s for s in SOURCES if s["region"] == "global" or s["region"] == region]
