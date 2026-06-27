"""Smart source setup: give a bare URL or a domain, and we find the RSS feed
automatically and let the LLM name and section it. Inspired by openpaper, where
you just say "add nrk.no" and the agent figures out the rest."""

import json
import re
from urllib.parse import urljoin, urlparse

import feedparser
import httpx

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
)

# Common feed paths to try if the site doesn't declare a <link>.
_COMMON_PATHS = [
    "/rss",
    "/feed",
    "/feed/",
    "/rss.xml",
    "/index.xml",
    "/atom.xml",
    "/feeds/all.atom.xml",
]

# Query-based feeds (no <link>, no standard path). Tried against the site root.
# E.g. Schibsted/Lab.no sites (kode24.no) serve RSS at "?lab_viewport=rss".
_COMMON_QUERY_FEEDS = [
    "?lab_viewport=rss",
]


def _site_root(url: str) -> str:
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}/"


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


# Map RSS/Atom language tags to the app's ISO codes. Feeds declare things like
# "fr-FR", "en_US" or — for Norwegian — "nb"/"nn"/"no"; we collapse the regional
# suffix and fold the Norwegian written-standard codes onto the app's "no".
_LANG_ALIASES = {"nb": "no", "nn": "no", "nob": "no", "nno": "no"}


def _norm_lang(raw: str) -> str:
    """Normalize a feed <language> tag to a 2-letter ISO code, or "" if unknown."""
    base = (raw or "").strip().lower().replace("_", "-").split("-")[0]
    base = _LANG_ALIASES.get(base, base)
    return base if base.isalpha() and len(base) == 2 else ""


# Country-code TLD -> language, used as a fallback only when the feed declares no
# <language> (common for Norwegian feeds: NRK, Digi.no, Aftenposten, …). Limited
# to languages the app can translate, and to TLDs that map cleanly to one of them.
_TLD_LANGS = {
    "no": "no", "fr": "fr", "de": "de", "at": "de",
    "se": "sv", "dk": "da", "es": "es",
}


def _lang_from_url(url: str) -> str:
    """Guess language from a URL's country-code TLD, or "" if it doesn't map."""
    host = urlparse(url).netloc.lower().split(":")[0]
    tld = host.rsplit(".", 1)[-1] if "." in host else ""
    return _TLD_LANGS.get(tld, "")


def _validate_feed(url: str) -> tuple[str, int, str] | None:
    txt = _fetch(url)
    if not txt:
        return None
    fp = feedparser.parse(txt)
    if fp.entries:
        return (fp.feed.get("title", ""), len(fp.entries), _norm_lang(fp.feed.get("language", "")))
    return None


def discover_feeds(site_url: str) -> tuple[str | None, list[dict]]:
    """Returns (the page's HTML, list of working feeds)."""
    html = _fetch(site_url)
    candidates: list[str] = [site_url]  # maybe the URL is already a feed
    candidates += _declared_feeds(site_url, html)
    candidates += [urljoin(site_url, p) for p in _COMMON_PATHS]
    candidates += [_site_root(site_url) + q for q in _COMMON_QUERY_FEEDS]

    seen: set[str] = set()
    working: list[dict] = []
    for c in candidates:
        if c in seen:
            continue
        seen.add(c)
        res = _validate_feed(c)
        if res:
            working.append({"url": c, "title": res[0], "entries": res[1], "lang": res[2]})
        if len(working) >= 5:
            break
    return html, working


def detect_playwright_source(site: str, title: str) -> dict | None:
    """Feedless site: render with Playwright, let the LLM suggest a link
    selector, and validate it by actually running it. Requires Playwright + LLM."""
    from .. import i18n, llm, runtime_config
    from .browser import BrowserSession, playwright_available

    if not (playwright_available() and llm.enabled()):
        return None
    target = i18n.lang_prompt_name(runtime_config.paper_lang())
    try:
        with BrowserSession() as bs:
            candidates = bs.link_candidates(site)
            if not candidates:
                return None
            sug = llm.suggest_selector(site, title, candidates, target=target)
            if not sug or not sug.get("link_selector"):
                return None
            selector = sug["link_selector"]
            links = bs.links(site, selector)
    except Exception as e:
        print(f"[discover] playwright detection failed: {e}")
        return None

    # Validate: enough links with meaningful text?
    good = [(h, t) for h, t in links if t and len(t.strip()) >= 20]
    if len(good) < 5:
        return None
    return {
        "ok": True,
        "name": (sug.get("name") or title or urlparse(site).netloc).strip()[:80],
        "kind": "playwright",
        "url": site,
        "section": sug.get("section") or "News",
        "entries": len(good),
        "config": json.dumps({"link_selector": selector}),
    }


def propose(user_input: str) -> dict:
    """Proposes (and validates) a source config from a bare URL/domain.
    Returns {ok, name, kind, url, section, entries} or {ok: False, reason}."""
    site = normalize_url(user_input)
    if not site:
        return {"ok": False, "reason": "Empty input."}

    html, feeds = discover_feeds(site)
    title = _page_title(html)

    from .. import i18n, llm, runtime_config

    target = i18n.lang_prompt_name(runtime_config.paper_lang())
    # Found no feed automatically? Ask the LLM to guess a known feed URL and validate.
    if not feeds:
        guess = llm.suggest_feed_url(site, title, target=target)
        if guess and guess.get("url"):
            res = _validate_feed(guess["url"])
            if res:
                return {
                    "ok": True,
                    "name": (guess.get("name") or res[0] or title or urlparse(site).netloc).strip()[:80],
                    "kind": "rss",
                    "url": guess["url"],
                    "section": guess.get("section") or "News",
                    "entries": res[1],
                    "lang": res[2] or _lang_from_url(guess["url"]),
                }
        # No feed to find: try to auto-detect a Playwright selector.
        pw = detect_playwright_source(site, title)
        if pw:
            return pw
        return {
            "ok": False,
            "reason": (
                f"Found neither an RSS feed nor a usable article selector on {site}. "
                f"Add it manually below."
            ),
        }

    # Let the LLM choose the best feed + name + section if available.
    choice = llm.choose_source(site, title, feeds, target=target)
    if choice and choice.get("url"):
        url = choice["url"]
        name = choice.get("name") or title or urlparse(site).netloc
        section = choice.get("section") or "News"
    else:
        best = feeds[0]
        url = best["url"]
        name = best["title"] or title or urlparse(site).netloc
        section = "News"

    chosen = next((f for f in feeds if f["url"] == url), feeds[0])
    return {
        "ok": True,
        "name": name.strip()[:80],
        "kind": "rss",
        "url": url,
        "section": section,
        "entries": chosen["entries"],
        "lang": chosen.get("lang") or _lang_from_url(url),
    }
