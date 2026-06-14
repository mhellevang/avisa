"""Smart kilde-oppsett: gi en bar URL eller et domene, så finner vi RSS-feeden
automatisk og lar LLM-en navngi og seksjonere den. Inspirert av openpaper, der
du bare sier «add nrk.no» og agenten finner ut av resten."""

import json
import re
from urllib.parse import urljoin, urlparse

import feedparser
import httpx

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
)

# Vanlige feed-stier å prøve hvis siden ikke deklarerer en <link>.
_COMMON_PATHS = [
    "/rss",
    "/feed",
    "/feed/",
    "/rss.xml",
    "/index.xml",
    "/atom.xml",
    "/feeds/all.atom.xml",
]


def normalize_url(raw: str) -> str:
    raw = (raw or "").strip()
    if not raw:
        return ""
    if not raw.startswith(("http://", "https://")):
        raw = "https://" + raw
    return raw


def _fetch(url: str) -> str | None:
    try:
        r = httpx.get(url, timeout=15.0, follow_redirects=True, headers={"User-Agent": _UA})
        r.raise_for_status()
        return r.text
    except Exception:
        return None


def _page_title(html: str | None) -> str:
    m = re.search(r"<title[^>]*>(.*?)</title>", html or "", re.I | re.S)
    return (m.group(1).strip() if m else "")[:120]


def _declared_feeds(base_url: str, html: str | None) -> list[str]:
    feeds: list[str] = []
    for tag in re.findall(r"<link[^>]+>", html or "", re.I):
        if re.search(r'type=["\']application/(rss|atom)\+xml', tag, re.I):
            href = re.search(r'href=["\']([^"\']+)["\']', tag, re.I)
            if href:
                feeds.append(urljoin(base_url, href.group(1)))
    return feeds


def _validate_feed(url: str) -> tuple[str, int] | None:
    txt = _fetch(url)
    if not txt:
        return None
    fp = feedparser.parse(txt)
    if fp.entries:
        return (fp.feed.get("title", ""), len(fp.entries))
    return None


def discover_feeds(site_url: str) -> tuple[str | None, list[dict]]:
    """Returnerer (sidens HTML, liste av fungerende feeder)."""
    html = _fetch(site_url)
    candidates: list[str] = [site_url]  # kanskje URL-en alt er en feed
    candidates += _declared_feeds(site_url, html)
    candidates += [urljoin(site_url, p) for p in _COMMON_PATHS]

    seen: set[str] = set()
    working: list[dict] = []
    for c in candidates:
        if c in seen:
            continue
        seen.add(c)
        res = _validate_feed(c)
        if res:
            working.append({"url": c, "title": res[0], "entries": res[1]})
        if len(working) >= 5:
            break
    return html, working


def detect_playwright_source(site: str, title: str) -> dict | None:
    """Feedløs side: render med Playwright, la LLM foreslå en link-selector, og
    valider den ved å faktisk kjøre den. Krever Playwright + LLM."""
    from .. import llm
    from .browser import BrowserSession, playwright_available

    if not (playwright_available() and llm.enabled()):
        return None
    try:
        with BrowserSession() as bs:
            candidates = bs.link_candidates(site)
            if not candidates:
                return None
            sug = llm.suggest_selector(site, title, candidates)
            if not sug or not sug.get("link_selector"):
                return None
            selector = sug["link_selector"]
            links = bs.links(site, selector)
    except Exception as e:
        print(f"[discover] playwright-deteksjon feilet: {e}")
        return None

    # Valider: nok lenker med meningsfull tekst?
    good = [(h, t) for h, t in links if t and len(t.strip()) >= 20]
    if len(good) < 5:
        return None
    return {
        "ok": True,
        "name": (sug.get("name") or title or urlparse(site).netloc).strip()[:80],
        "kind": "playwright",
        "url": site,
        "section": sug.get("section") or "Nyheter",
        "entries": len(good),
        "config": json.dumps({"link_selector": selector}),
    }


def propose(user_input: str) -> dict:
    """Foreslår (og validerer) en kildekonfig fra en bar URL/domene.
    Returnerer {ok, name, kind, url, section, entries} eller {ok: False, reason}."""
    site = normalize_url(user_input)
    if not site:
        return {"ok": False, "reason": "Tom input."}

    html, feeds = discover_feeds(site)
    title = _page_title(html)

    from .. import llm

    # Fant vi ingen feed automatisk? Be LLM gjette en kjent feed-URL og valider.
    if not feeds:
        guess = llm.suggest_feed_url(site, title)
        if guess and guess.get("url"):
            res = _validate_feed(guess["url"])
            if res:
                return {
                    "ok": True,
                    "name": (guess.get("name") or res[0] or title or urlparse(site).netloc).strip()[:80],
                    "kind": "rss",
                    "url": guess["url"],
                    "section": guess.get("section") or "Nyheter",
                    "entries": res[1],
                }
        # Ingen feed å finne: prøv å auto-detektere en Playwright-selector.
        pw = detect_playwright_source(site, title)
        if pw:
            return pw
        return {
            "ok": False,
            "reason": (
                f"Fant verken RSS-feed eller en brukbar artikkel-selector på {site}. "
                f"Legg den til manuelt nedenfor."
            ),
        }

    # La LLM velge beste feed + navn + seksjon hvis tilgjengelig.
    choice = llm.choose_source(site, title, feeds)
    if choice and choice.get("url"):
        url = choice["url"]
        name = choice.get("name") or title or urlparse(site).netloc
        section = choice.get("section") or "Nyheter"
    else:
        best = feeds[0]
        url = best["url"]
        name = best["title"] or title or urlparse(site).netloc
        section = "Nyheter"

    entries = next((f["entries"] for f in feeds if f["url"] == url), feeds[0]["entries"])
    return {
        "ok": True,
        "name": name.strip()[:80],
        "kind": "rss",
        "url": url,
        "section": section,
        "entries": entries,
    }
