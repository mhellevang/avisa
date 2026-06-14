"""Tynt lag mot OpenRouter for kuratering og oversettelse.

Designprinsipp: appen skal fungere ende-til-ende UTEN nøkkel. Da faller
kuratering tilbake på nyeste saker, og oversettelse hopper over (originaltekst
beholdes). Med nøkkel brukes LLM-en.
"""

import json
import shutil
import subprocess
from typing import Optional

import httpx

from .config import settings

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

_claude_available: Optional[bool] = None


def _claude_cli_available() -> bool:
    global _claude_available
    if _claude_available is None:
        _claude_available = shutil.which("claude") is not None
    return _claude_available


def active_provider() -> str:
    """Løser opp 'auto' til en konkret provider. På localhost med en innlogget
    Claude-session brukes claude-CLI når ingen OpenRouter-nøkkel er satt."""
    p = settings.llm_provider.lower()
    if p != "auto":
        return p
    if settings.openrouter_api_key.strip():
        return "openrouter"
    if _claude_cli_available():
        return "claude_cli"
    return "none"


def enabled() -> bool:
    return active_provider() != "none"


def provider_label() -> str:
    return {
        "openrouter": "OpenRouter",
        "claude_cli": "lokal Claude-session",
        "none": "ingen (demo-modus)",
    }.get(active_provider(), active_provider())


def _chat_openrouter(model: str, system: str, user: str, max_tokens: int) -> Optional[str]:
    try:
        resp = httpx.post(
            OPENROUTER_URL,
            headers={
                "Authorization": f"Bearer {settings.openrouter_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": 0.3,
                "max_tokens": max_tokens,
            },
            timeout=120.0,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]
    except Exception as e:
        print(f"[llm] openrouter feilet ({model}): {e}")
        return None


def _chat_claude_cli(system: str, user: str) -> Optional[str]:
    """Kaller den lokale, innloggede claude-CLI-en. Prompten sendes på stdin
    (tåler lange tekster); system-prompt via flagg."""
    cmd = ["claude", "-p", "--output-format", "text"]
    if system:
        cmd += ["--append-system-prompt", system]
    if settings.claude_model.strip():
        cmd += ["--model", settings.claude_model.strip()]
    try:
        proc = subprocess.run(
            cmd, input=user, capture_output=True, text=True, timeout=180
        )
        if proc.returncode != 0:
            print(f"[llm] claude-cli feilet: {proc.stderr[:200]}")
            return None
        return proc.stdout.strip()
    except Exception as e:
        print(f"[llm] claude-cli unntak: {e}")
        return None


def _chat(model: str, system: str, user: str, max_tokens: int = 2000) -> Optional[str]:
    provider = active_provider()
    if provider == "openrouter":
        return _chat_openrouter(model, system, user, max_tokens)
    if provider == "claude_cli":
        return _chat_claude_cli(system, user)
    return None


def _extract_json(text: Optional[str]):
    """Plukker ut JSON fra et LLM-svar, robust mot ```json-fences og prat
    rundt."""
    if not text:
        return None
    t = text.strip()
    if t.startswith("```"):
        t = t.strip("`")
        if t.lstrip().lower().startswith("json"):
            t = t.lstrip()[4:]
        t = t.strip()
    # 1) Vanligst: hele strengen er gyldig JSON.
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    # 2) Ellers: finn ytterste objekt/array. Velg den klammen som kommer FØRST,
    #    så vi ikke plukker en indre array (f.eks. "actions": []) i et objekt.
    pairs = [("{", "}"), ("[", "]")]
    pairs.sort(key=lambda p: (t.find(p[0]) if p[0] in t else len(t) + 1))
    for open_c, close_c in pairs:
        start = t.find(open_c)
        end = t.rfind(close_c)
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(t[start : end + 1])
            except json.JSONDecodeError:
                continue
    return None


# --------------------------------------------------------------------------- #
# Kuratering
# --------------------------------------------------------------------------- #
def curate_articles(articles, preferences: str, n: int) -> list[dict]:
    """Returnerer liste av {id, score, section, reason} for de valgte sakene."""
    # Sorter etter ferskhet og kapp til et fornuftig antall kandidater.
    cands = sorted(
        articles,
        key=lambda a: a.published_at or a.fetched_at,
        reverse=True,
    )[:60]

    if not enabled():
        top = cands[:n]
        return [
            {
                "id": a.id,
                "score": round(1.0 - i * 0.01, 3),
                "section": a.section,
                "reason": "Nyeste sak (ingen LLM-nøkkel satt)",
            }
            for i, a in enumerate(top)
        ]

    listing = "\n".join(
        f"{a.id}\t[{a.section}] {a.title} — {(a.summary or '')[:180]}" for a in cands
    )
    system = (
        "Du er en erfaren nyhetsredaktør som setter sammen en personlig "
        "morgenavis for én leser. Du velger ut de viktigste og mest "
        "relevante sakene, unngår duplikater, og sørger for en balansert "
        "miks av seksjoner."
    )
    user = (
        f"Leserens redaksjonelle profil:\n{preferences}\n\n"
        f"Velg de {n} beste sakene fra kandidatlisten under. Ranger dem, gi "
        f"hver en score mellom 0 og 1, plasser dem i en passende seksjon "
        f"(f.eks. Innenriks, Utenriks, Teknologi, Vitenskap, Klima, Økonomi, "
        f"Kultur), og gi en kort begrunnelse.\n\n"
        f"Kandidater (id<TAB>tekst):\n{listing}\n\n"
        f'Svar KUN med JSON på formen: '
        f'[{{"id": <int>, "score": <0-1>, "section": "<str>", "reason": "<str>"}}]'
    )
    data = _extract_json(_chat(settings.curate_model, system, user, max_tokens=2500))
    if not isinstance(data, list):
        # Fallback hvis LLM svarer rart
        top = cands[:n]
        return [
            {"id": a.id, "score": 0.5, "section": a.section, "reason": "Fallback"}
            for a in top
        ]
    # Rens opp og kapp
    cleaned = []
    for r in data:
        if isinstance(r, dict) and "id" in r:
            cleaned.append(r)
    return cleaned[:n]


# --------------------------------------------------------------------------- #
# Oversettelse til norsk
# --------------------------------------------------------------------------- #
def translate_to_norwegian(
    title: str, summary: str, content: str = ""
) -> Optional[dict]:
    """Returnerer {title, summary, content} på norsk bokmål, eller None hvis
    ingen nøkkel / kallet feiler. Brødtekst kappes for å holde kostnaden nede."""
    if not enabled():
        return None
    body = (content or "")[: settings.translate_body_max_chars]
    system = (
        "Du er en profesjonell oversetter. Oversett til naturlig norsk bokmål. "
        "Behold egennavn og avsnittsinndeling. Hvis teksten allerede er på "
        "norsk, returner den uendret. Ikke legg til kommentarer."
    )
    user = (
        "Oversett feltene under til norsk bokmål. Behold linjeskift i 'content'. "
        "Svar KUN med JSON: "
        '{"title": "<tittel>", "summary": "<ingress>", "content": "<brødtekst>"}\n\n'
        f"title: {title}\n"
        f"summary: {summary}\n"
        f"content:\n{body}"
    )
    # Mer tokens når vi også oversetter brødtekst.
    max_tokens = 6000 if body else 1200
    data = _extract_json(_chat(settings.translate_model, system, user, max_tokens=max_tokens))
    if isinstance(data, dict) and "title" in data:
        return data
    return None


def translate_batch(items: list[dict]) -> dict[int, dict]:
    """Oversetter flere artikler i ÉTT kall. items: [{id, title, summary, content}].
    Returnerer {id: {title, summary, content}}. Amortiserer den dyre
    claude-CLI-oppstarten over flere artikler. {} uten LLM eller ved feil."""
    if not enabled() or not items:
        return {}

    blocks = []
    total_chars = 0
    for it in items:
        body = (it.get("content") or "")[: settings.translate_body_max_chars]
        total_chars += len(body) + len(it.get("summary") or "") + len(it.get("title") or "")
        blocks.append(
            f"=== ARTIKKEL {it['id']} ===\n"
            f"TITTEL: {it.get('title', '')}\n"
            f"INGRESS: {it.get('summary', '')}\n"
            f"BRØDTEKST:\n{body}"
        )
    system = (
        "Du er en profesjonell oversetter. Oversett til naturlig norsk bokmål. "
        "Behold egennavn og avsnittsinndeling. Tekst som alt er på norsk beholdes "
        "uendret."
    )
    user = (
        "Oversett HVER artikkel under til norsk bokmål. Behold linjeskift i "
        "brødteksten, og behold id-en for hver artikkel.\n"
        'Svar KUN med en JSON-array: '
        '[{"id": <int>, "title": "<tittel>", "summary": "<ingress>", "content": "<brødtekst>"}]\n\n'
        + "\n\n".join(blocks)
    )
    max_tokens = min(8000, 1200 + int(total_chars * 0.6))
    data = _extract_json(_chat(settings.translate_model, system, user, max_tokens=max_tokens))
    out: dict[int, dict] = {}
    if isinstance(data, list):
        for d in data:
            if isinstance(d, dict) and "id" in d:
                try:
                    out[int(d["id"])] = d
                except (TypeError, ValueError):
                    continue
    return out


def translate_headlines_batch(items: list[dict]) -> dict[int, dict]:
    """Oversetter KUN tittel+ingress for flere artikler i ÉTT kall — billig
    foroversetting for «flere saker»-lista. items: [{id, title, summary}].
    Returnerer {id: {title, summary}}. {} uten LLM eller ved feil."""
    if not enabled() or not items:
        return {}

    blocks = []
    for it in items:
        blocks.append(
            f"=== ARTIKKEL {it['id']} ===\n"
            f"TITTEL: {it.get('title', '')}\n"
            f"INGRESS: {it.get('summary', '')}"
        )
    system = (
        "Du er en profesjonell oversetter. Oversett til naturlig norsk bokmål. "
        "Behold egennavn. Tekst som alt er på norsk beholdes uendret."
    )
    user = (
        "Oversett tittel og ingress for HVER artikkel under til norsk bokmål, "
        "og behold id-en.\n"
        'Svar KUN med en JSON-array: '
        '[{"id": <int>, "title": "<tittel>", "summary": "<ingress>"}]\n\n'
        + "\n\n".join(blocks)
    )
    max_tokens = min(4000, 600 + len(items) * 200)
    data = _extract_json(_chat(settings.translate_model, system, user, max_tokens=max_tokens))
    out: dict[int, dict] = {}
    if isinstance(data, list):
        for d in data:
            if isinstance(d, dict) and "id" in d:
                try:
                    out[int(d["id"])] = d
                except (TypeError, ValueError):
                    continue
    return out


def translate_body(title: str, content: str) -> Optional[str]:
    """Oversetter KUN brødteksten til norsk bokmål (tittel som kontekst).
    Returnerer teksten, eller None uten LLM / ved feil. Brukt ved åpning av
    saker som ikke alt er foroversatt."""
    if not enabled() or not content:
        return None
    body = content[: settings.translate_body_max_chars]
    system = (
        "Du er en profesjonell oversetter. Oversett til naturlig norsk bokmål. "
        "Behold egennavn og avsnittsinndeling. Hvis teksten alt er på norsk, "
        "returner den uendret. Ikke legg til kommentarer."
    )
    user = (
        "Oversett brødteksten under til norsk bokmål. Behold linjeskift. "
        'Svar KUN med JSON: {"content": "<brødtekst>"}\n\n'
        f"TITTEL (kontekst): {title}\n"
        f"BRØDTEKST:\n{body}"
    )
    data = _extract_json(_chat(settings.translate_model, system, user, max_tokens=6000))
    if isinstance(data, dict) and isinstance(data.get("content"), str):
        return data["content"]
    return None


# --------------------------------------------------------------------------- #
# Tilbakemelding → revidert redaksjonell profil
# --------------------------------------------------------------------------- #
def revise_preferences(current: str, feedback: str) -> Optional[str]:
    """Tar leserens frie tilbakemelding og dagens profil, og returnerer en
    oppdatert profil (ren tekst). None hvis ingen LLM / feiler."""
    if not enabled():
        return None
    system = (
        "Du vedlikeholder en kort redaksjonell profil for en personlig avis. "
        "Profilen styrer hvilke saker som velges. Innarbeid leserens "
        "tilbakemelding i profilen — juster vekting, legg til eller fjern temaer "
        "— og hold den konsis (noen få setninger). Behold det som fortsatt gjelder."
    )
    user = (
        f"Nåværende profil:\n{current}\n\n"
        f"Leserens tilbakemelding:\n{feedback}\n\n"
        "Returner KUN den oppdaterte profilen som ren tekst på norsk, uten "
        "forklaring eller anførselstegn."
    )
    out = _chat(settings.curate_model, system, user, max_tokens=600)
    return out.strip() if out else None


# --------------------------------------------------------------------------- #
# Smart kilde-oppsett: velg beste feed + navngi + seksjoner
# --------------------------------------------------------------------------- #
def choose_source(site_url: str, title: str, feeds: list[dict]) -> Optional[dict]:
    """Velger beste RSS-feed og foreslår navn + seksjon. None uten LLM."""
    if not enabled():
        return None
    listing = "\n".join(
        f'{i + 1}. {f["url"]}  (tittel: {f["title"] or "?"}, {f["entries"]} saker)'
        for i, f in enumerate(feeds)
    )
    system = "Du hjelper med å konfigurere en nyhetskilde for en personlig avis."
    user = (
        f"Nettsted: {site_url}\n"
        f"Sidetittel: {title}\n"
        f"Fungerende RSS-feeder:\n{listing}\n\n"
        "Velg den beste feeden for en generell nyhetsleser (helst hovedfeeden, "
        "ikke en smal underkategori). Gi kilden et kort navn på norsk, og foreslå "
        "én seksjon (Innenriks, Utenriks, Teknologi, Vitenskap, Klima, Økonomi, "
        "Kultur, Sport eller Nyheter).\n"
        'Svar KUN med JSON: {"url": "<feed-url>", "name": "<navn>", "section": "<seksjon>"}'
    )
    data = _extract_json(_chat(settings.curate_model, system, user, max_tokens=300))
    if isinstance(data, dict) and data.get("url"):
        return data
    return None


def interpret_config(
    text: str,
    sources: list,
    preferences: str,
    title: str,
    front_page_size: int,
    poll_minutes: int,
    history: Optional[list] = None,
) -> Optional[dict]:
    """Tolker en brukers frie konfig-melding til {reply, actions}. 'reply' er et
    naturlig svar til brukeren; 'actions' er konkrete endringer. Svarer også på
    spørsmål (da er actions tom). None uten LLM / hvis svaret ikke kan tolkes."""
    if not enabled():
        return None
    src_list = "\n".join(
        f'- "{s.name}" ({s.kind}, {"på" if s.enabled else "av"})' for s in sources
    ) or "(ingen kilder ennå)"
    transcript = ""
    for m in (history or [])[-6:]:
        who = "Bruker" if m.get("role") == "user" else "Assistent"
        transcript += f"{who}: {m.get('text', '')}\n"
    # Rammet som en datakonverterings-oppgave (ikke rollespill) — ellers kan
    # claude-CLI-en avvise den som «prompt injection» mot Claude Code-personaen.
    system = (
        "Du utfører en datakonverteringsoppgave for et nyhetsprogram. Du leser en "
        "brukermelding og produserer ett JSON-objekt i nøyaktig angitt format."
    )
    user = (
        "Konteksten under beskriver oppsettet til en personlig avis. Tolk den "
        "siste brukermeldingen og produser ett JSON-objekt med et vennlig svar "
        "på norsk ('reply') og hvilke endringer som skal gjøres ('actions').\n\n"
        f"KILDER:\n{src_list}\n\n"
        f"PROFIL: {preferences}\n"
        f"TITTEL: {title} · FORSIDESTØRRELSE: {front_page_size} · "
        f"POLL_MIN: {poll_minutes}\n\n"
        + (f"SAMTALE SÅ LANGT:\n{transcript}\n" if transcript else "")
        + f"SISTE BRUKERMELDING:\n{text}\n\n"
        "Produser kun dette JSON-objektet, uten annen tekst:\n"
        '{"reply": "<kort svar på norsk>", "actions": [<0 eller flere handlinger>]}\n\n'
        "Gyldige handlinger (objekter i actions-arrayen):\n"
        '{"action":"add_source","query":"<navn eller url, f.eks. nrk.no>"}\n'
        '{"action":"remove_source","name":"<eksakt kildenavn fra KILDER>"}\n'
        '{"action":"enable_source","name":"<kildenavn>"}\n'
        '{"action":"disable_source","name":"<kildenavn>"}\n'
        '{"action":"set_preferences","value":"<hele den nye profilen>"}\n'
        '{"action":"set_title","value":"<tittel>"}\n'
        '{"action":"set_front_page_size","value":<heltall>}\n'
        '{"action":"set_poll_minutes","value":<heltall>}\n\n'
        "Regler: Ved mer/mindre av temaer, bruk set_preferences med en oppdatert "
        "profil som BEHOLDER det som fortsatt gjelder. Bruk eksakte kildenavn fra "
        "KILDER. Er meldingen et spørsmål, svar i 'reply' og la 'actions' være []."
    )
    # Ett retry — CLI-en kan av og til avvike fra formatet.
    for _ in range(2):
        data = _extract_json(_chat(settings.curate_model, system, user, max_tokens=1000))
        if isinstance(data, dict) and "actions" in data:
            if not isinstance(data["actions"], list):
                data["actions"] = []
            return data
    return None


def suggest_selector(site_url: str, title: str, candidates: list[dict]) -> Optional[dict]:
    """Foreslår en CSS-selector for artikkel-lenker på en feedløs side, basert
    på en prøve av <a>-lenker (tekst + class). None uten LLM."""
    if not enabled():
        return None
    lines = []
    for i, c in enumerate(candidates[:30]):
        lines.append(
            f'{i + 1}. text="{c.get("text", "")[:70]}" '
            f'class="{c.get("cls", "")[:50]}" '
            f'parentClass="{c.get("parentCls", "")[:50]}"'
        )
    listing = "\n".join(lines)
    system = (
        "Du er ekspert på HTML/CSS og finner robuste selektorer for "
        "artikkel-lenker på nyhetssider."
    )
    user = (
        f"Nettsted: {site_url}\nSidetittel: {title}\n\n"
        f"Under er <a>-lenker fra forsiden med tekst og class-attributter:\n{listing}\n\n"
        "Foreslå ÉN CSS-selector som treffer artikkel-/overskriftslenkene (ikke "
        "meny, footer, annonser eller relaterte-lenker). Foretrekk class-baserte "
        "selektorer som fanger flest mulig av sakene. Gi også et kort norsk navn "
        "og en seksjon.\n"
        'Svar KUN med JSON: {"link_selector": "<css>", "name": "<navn>", "section": "<seksjon>"}'
    )
    data = _extract_json(_chat(settings.curate_model, system, user, max_tokens=300))
    if isinstance(data, dict) and data.get("link_selector"):
        return data
    return None


def suggest_feed_url(site_url: str, title: str) -> Optional[dict]:
    """Når ingen feed ble funnet automatisk: spør LLM om en sannsynlig
    RSS-feed-URL (mange er kjente, f.eks. BBC sin feeds.bbci.co.uk). Resultatet
    valideres etterpå. None uten LLM."""
    if not enabled():
        return None
    system = "Du kjenner RSS-feedene til store nyhetsnettsteder."
    user = (
        f"Nettsted: {site_url}\nSidetittel: {title}\n\n"
        "Oppgi den mest sannsynlige RSS-feed-URL-en for hovednyhetene på dette "
        "nettstedet (full URL). Foreslå også et kort norsk navn og en seksjon. "
        "Hvis du ikke kjenner en feed, sett url til tom streng.\n"
        'Svar KUN med JSON: {"url": "<feed-url eller tom>", "name": "<navn>", "section": "<seksjon>"}'
    )
    data = _extract_json(_chat(settings.curate_model, system, user, max_tokens=300))
    if isinstance(data, dict) and data.get("url"):
        return data
    return None
