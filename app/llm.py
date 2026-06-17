"""Thin layer over OpenRouter for curation and translation.

Design principle: the app must work end-to-end WITHOUT a key. Then curation
falls back to the latest stories and translation is skipped (the original text
is kept). With a key, the LLM is used.
"""

import json
import re
import shutil
import subprocess
from typing import Optional

import httpx

from . import i18n
from .config import settings

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

_claude_available: Optional[bool] = None


def _claude_cli_available() -> bool:
    global _claude_available
    if _claude_available is None:
        _claude_available = shutil.which("claude") is not None
    return _claude_available


def active_provider() -> str:
    """Resolves 'auto' to a concrete provider. On localhost with a logged-in
    Claude session, the claude CLI is used when no OpenRouter key is set."""
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
        "claude_cli": "local Claude session",
        "none": "none (demo mode)",
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
        print(f"[llm] openrouter failed ({model}): {e}")
        return None


def _chat_claude_cli(system: str, user: str) -> Optional[str]:
    """Calls the local, logged-in claude CLI. The prompt is sent on stdin
    (handles long texts); the system prompt via a flag."""
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
            print(f"[llm] claude-cli failed: {proc.stderr[:200]}")
            return None
        return proc.stdout.strip()
    except Exception as e:
        print(f"[llm] claude-cli exception: {e}")
        return None


def _chat(model: str, system: str, user: str, max_tokens: int = 2000) -> Optional[str]:
    provider = active_provider()
    if provider == "openrouter":
        return _chat_openrouter(model, system, user, max_tokens)
    if provider == "claude_cli":
        return _chat_claude_cli(system, user)
    return None


def _extract_json(text: Optional[str]):
    """Extracts JSON from an LLM reply, robust against ```json fences and
    surrounding chatter."""
    if not text:
        return None
    t = text.strip()
    if t.startswith("```"):
        t = t.strip("`")
        if t.lstrip().lower().startswith("json"):
            t = t.lstrip()[4:]
        t = t.strip()
    # 1) Most common: the whole string is valid JSON.
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    # 2) Otherwise: find the outermost object/array. Pick the bracket that comes
    #    FIRST, so we don't grab an inner array (e.g. "actions": []) in an object.
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
# Curation
# --------------------------------------------------------------------------- #
def curate_articles(
    articles, preferences: str, n: int, target: str = "English",
    source_names: Optional[dict] = None, today: str = "",
) -> list[dict]:
    """Returns a list of {id, score, section, reason, deck} for the selected
    stories. Section names, reasons and decks are written in `target`.
    `source_names` maps source_id -> name so the editor can honour the
    source-diversity cap and is included in the candidate listing."""
    source_names = source_names or {}
    # Sort by recency and cap to a sensible number of candidates.
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
                "reason": i18n.current("Latest story (no LLM key set)"),
                "deck": "",
            }
            for i, a in enumerate(top)
        ]

    def _src(a) -> str:
        return source_names.get(a.source_id, "") or "?"

    listing = "\n".join(
        f"{a.id}\t[{a.section} · {_src(a)}] {a.title} — {(a.summary or '')[:180]}"
        for a in cands
    )
    system = (
        "You are an experienced news editor assembling a personal morning paper "
        "for a single reader. You pick the most important and relevant stories "
        "and compose a balanced, varied front page — not just the loudest topic."
    )
    user = (
        f"The reader's editorial profile:\n{preferences}\n\n"
        f"Pick the {n} best stories from the candidate list below and rank them "
        f"(best first). For each, give a score between 0 and 1, place it in a "
        f"suitable section (e.g. World, Domestic, Technology, Science, Climate, "
        f"Economy, Culture), and give a short reason it was chosen.\n\n"
        f"Composition rules (balance, don't just take the top scorers):\n"
        f"- No single topic/section may exceed ~30% of the selection.\n"
        f"- No single source may exceed ~40% of the selection.\n"
        f"- Reserve roughly 20% for worthwhile discoveries outside the reader's "
        f"stated interests, so the paper isn't an echo chamber.\n"
        f"- Cover at least 3 distinct sections.\n"
        f"- Avoid near-duplicate stories about the same event.\n\n"
        + (
            f"The profile may include a `## Feedback` section with dated signals "
            f"(more/less/love/hide + a topic) and free notes. Today is {today}. "
            f"Apply them when ranking: 'love' strongly boosts that topic, 'more' "
            f"boosts it, 'less' reduces it, 'hide' excludes that topic/source "
            f"entirely. Weight recent feedback fully, halve it for signals older "
            f"than 30 days, and quarter it beyond 90 days.\n\n"
            if today else ""
        )
        + f"For the few most important stories (the likely lead and majors), also "
        f"write a one-sentence `deck` — an editorial subtitle that adds context "
        f"beyond the headline. Leave `deck` an empty string for minor stories.\n\n"
        f"Write section names, reasons and decks in {target}.\n\n"
        f"Candidates (id<TAB>[section · source] title — summary):\n{listing}\n\n"
        f'Respond ONLY with JSON of the form: '
        f'[{{"id": <int>, "score": <0-1>, "section": "<str>", "reason": "<str>", "deck": "<str>"}}]'
    )
    data = _extract_json(_chat(settings.curate_model, system, user, max_tokens=3000))
    if not isinstance(data, list):
        # Fallback if the LLM responds oddly.
        top = cands[:n]
        return [
            {"id": a.id, "score": 0.5, "section": a.section,
             "reason": i18n.current("Fallback selection"), "deck": ""}
            for a in top
        ]
    # Clean up and cap.
    cleaned = []
    for r in data:
        if isinstance(r, dict) and "id" in r:
            cleaned.append(r)
    return cleaned[:n]


# --------------------------------------------------------------------------- #
# Translation
# --------------------------------------------------------------------------- #
_CODE_FENCE = re.compile(r"```.*?```", re.S)


def _mask_code(text: str) -> tuple[str, list[str]]:
    """Replaces fenced code blocks with sentinels before translation so the
    model never rewrites code (translating identifiers, reflowing lines, …).
    Returns the masked text and the removed blocks, restored with _restore_code.
    """
    blocks: list[str] = []

    def repl(m: "re.Match") -> str:
        blocks.append(m.group(0))
        return f"⟦CODE{len(blocks) - 1}⟧"

    return _CODE_FENCE.sub(repl, text), blocks


def _restore_code(text: str, blocks: list[str]) -> str:
    for i, block in enumerate(blocks):
        text = text.replace(f"⟦CODE{i}⟧", block)
    return text


def _translator_system(target: str, *, markdown: bool = False) -> str:
    """Shared system prompt for the translation calls. The model otherwise
    occasionally coins non-words (e.g. "Emergenesen" for "the emergence of") or
    carries over English forms ("urgensen"); the explicit "no invented words /
    anglicisms" clause targets exactly that. `markdown=True` adds the
    formatting-preservation clause for body text."""
    s = (
        f"You are a professional translator. Translate into natural, idiomatic {target}. "
        f"Use only established {target} words — never invent words or word-forms, and "
        f"never carry over English spellings or anglicisms (translate the meaning, not "
        f"word by word). Keep proper nouns and paragraph structure. If the text is "
        f"already in {target}, return it unchanged. Do not add comments. "
        f"Leave any ⟦CODE…⟧ placeholders exactly as they are."
    )
    if markdown:
        s += (
            " Preserve markdown formatting exactly — leave '#' heading markers and "
            "line breaks in place, translating only the text."
        )
    return s


def translate_fields(
    title: str, summary: str, content: str = "", target: str = "English"
) -> Optional[dict]:
    """Returns {title, summary, content} in the target language, or None if no
    key / the call fails. The body is capped to keep the cost down."""
    if not enabled():
        return None
    masked, code_blocks = _mask_code(content or "")
    body = masked[: settings.translate_body_max_chars]
    system = _translator_system(target)
    user = (
        f"Translate the fields below to {target}. Keep line breaks in 'content'. "
        "Respond ONLY with JSON: "
        '{"title": "<title>", "summary": "<lede>", "content": "<body>"}\n\n'
        f"title: {title}\n"
        f"summary: {summary}\n"
        f"content:\n{body}"
    )
    # More tokens when we also translate the body.
    max_tokens = 6000 if body else 1200
    data = _extract_json(_chat(settings.translate_model, system, user, max_tokens=max_tokens))
    if isinstance(data, dict) and "title" in data:
        if isinstance(data.get("content"), str):
            data["content"] = _restore_code(data["content"], code_blocks)
        return data
    return None


def translate_batch(items: list[dict], target: str = "English") -> dict[int, dict]:
    """Translates several articles in ONE call. items: [{id, title, summary, content}].
    Returns {id: {title, summary, content}}. Amortizes the expensive claude-CLI
    startup over several articles. {} without an LLM or on failure."""
    if not enabled() or not items:
        return {}

    blocks = []
    total_chars = 0
    for it in items:
        body = (it.get("content") or "")[: settings.translate_body_max_chars]
        total_chars += len(body) + len(it.get("summary") or "") + len(it.get("title") or "")
        blocks.append(
            f"=== ARTICLE {it['id']} ===\n"
            f"TITLE: {it.get('title', '')}\n"
            f"LEDE: {it.get('summary', '')}\n"
            f"BODY:\n{body}"
        )
    system = _translator_system(target)
    user = (
        f"Translate EACH article below to {target}. Keep line breaks in the "
        "body, and keep the id for each article.\n"
        'Respond ONLY with a JSON array: '
        '[{"id": <int>, "title": "<title>", "summary": "<lede>", "content": "<body>"}]\n\n'
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


def translate_headlines_batch(items: list[dict], target: str = "English") -> dict[int, dict]:
    """Translates ONLY title+lede for several articles in ONE call — cheap
    pre-translation for the "more stories" list. items: [{id, title, summary}].
    Returns {id: {title, summary}}. {} without an LLM or on failure."""
    if not enabled() or not items:
        return {}

    blocks = []
    for it in items:
        blocks.append(
            f"=== ARTICLE {it['id']} ===\n"
            f"TITLE: {it.get('title', '')}\n"
            f"LEDE: {it.get('summary', '')}"
        )
    system = _translator_system(target)
    user = (
        f"Translate the title and lede of EACH article below to {target}, "
        "and keep the id.\n"
        'Respond ONLY with a JSON array: '
        '[{"id": <int>, "title": "<title>", "summary": "<lede>"}]\n\n'
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


def translate_body(title: str, content: str, target: str = "English") -> Optional[str]:
    """Translates ONLY the body to the target language (title as context).
    Returns the text, or None without an LLM / on failure. Used when opening
    stories that aren't pre-translated yet."""
    if not enabled() or not content:
        return None
    masked, code_blocks = _mask_code(content)
    body = masked[: settings.translate_body_max_chars]
    system = _translator_system(target, markdown=True)
    user = (
        f"Translate the body below to {target}. Keep line breaks and markdown headings. "
        'Respond ONLY with JSON: {"content": "<body>"}\n\n'
        f"TITLE (context): {title}\n"
        f"BODY:\n{body}"
    )
    data = _extract_json(_chat(settings.translate_model, system, user, max_tokens=6000))
    if isinstance(data, dict) and isinstance(data.get("content"), str):
        return _restore_code(data["content"], code_blocks)
    return None


# --------------------------------------------------------------------------- #
# Feedback → revised editorial profile
# --------------------------------------------------------------------------- #
def revise_preferences(current: str, feedback: str, target: str = "English") -> Optional[str]:
    """Takes the reader's free-text feedback and the current profile, and
    returns an updated profile (plain text). None without an LLM / on failure."""
    if not enabled():
        return None
    system = (
        "You maintain a short editorial profile for a personal newspaper. The "
        "profile drives which stories are selected. Incorporate the reader's "
        "feedback into the profile — adjust weighting, add or remove topics — "
        "and keep it concise (a few sentences). Keep what still applies."
    )
    user = (
        f"Current profile:\n{current}\n\n"
        f"The reader's feedback:\n{feedback}\n\n"
        f"Return ONLY the updated profile as plain text in {target}, without "
        "explanation or quotation marks."
    )
    out = _chat(settings.curate_model, system, user, max_tokens=600)
    return out.strip() if out else None


def classify_feedback(feedback: str) -> list[dict]:
    """Turns the reader's free-text feedback into structured editorial signals
    the curator can apply with weights and time decay. Returns a list of
    {"signal": "more"|"less"|"love"|"hide", "topic": "<short topic/source>"}.
    Returns [] without an LLM or if nothing concrete could be extracted."""
    if not enabled():
        return []
    system = (
        "You convert a newspaper reader's free-text feedback into structured "
        "editorial signals. Signals: 'more' (want more of a topic), 'less' "
        "(want less), 'love' (strong positive), 'hide' (never show this "
        "topic/source). The topic is a short noun phrase or source name in the "
        "reader's own language."
    )
    user = (
        f"Reader feedback:\n{feedback}\n\n"
        "Extract every signal it expresses (there may be several). "
        'Respond ONLY with JSON of the form: '
        '[{"signal": "more|less|love|hide", "topic": "<short>"}]. '
        "If it expresses no concrete preference, respond with []."
    )
    data = _extract_json(_chat(settings.curate_model, system, user, max_tokens=400))
    if not isinstance(data, list):
        return []
    valid = {"more", "less", "love", "hide"}
    out = []
    for r in data:
        if isinstance(r, dict) and r.get("signal") in valid and r.get("topic"):
            out.append({"signal": r["signal"], "topic": str(r["topic"]).strip()})
    return out


# --------------------------------------------------------------------------- #
# Smart source setup: pick the best feed + name + section
# --------------------------------------------------------------------------- #
def choose_source(site_url: str, title: str, feeds: list[dict], target: str = "English") -> Optional[dict]:
    """Picks the best RSS feed and suggests a name + section. None without an LLM."""
    if not enabled():
        return None
    listing = "\n".join(
        f'{i + 1}. {f["url"]}  (title: {f["title"] or "?"}, {f["entries"]} stories)'
        for i, f in enumerate(feeds)
    )
    system = "You help configure a news source for a personal newspaper."
    user = (
        f"Website: {site_url}\n"
        f"Page title: {title}\n"
        f"Working RSS feeds:\n{listing}\n\n"
        "Pick the best feed for a general news reader (preferably the main feed, "
        f"not a narrow sub-category). Give the source a short name in {target}, "
        "and suggest one section (World, Domestic, Technology, Science, Climate, "
        "Economy, Culture, Sports or News).\n"
        'Respond ONLY with JSON: {"url": "<feed-url>", "name": "<name>", "section": "<section>"}'
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
    target: str = "English",
    topics: Optional[list] = None,
) -> Optional[dict]:
    """Interprets a user's free-text config message into {reply, actions}.
    'reply' is a natural reply to the user in the target language `target`;
    'actions' are concrete changes. Also answers questions (then actions is
    empty). None without an LLM / if the reply can't be parsed."""
    if not enabled():
        return None
    src_list = "\n".join(
        f'- "{s.name}" ({s.kind}, {"on" if s.enabled else "off"})' for s in sources
    ) or "(no sources yet)"
    topic_list = ", ".join(t["key"] for t in (topics or [])) or "(none)"
    transcript = ""
    for m in (history or [])[-6:]:
        who = "User" if m.get("role") == "user" else "Assistant"
        transcript += f"{who}: {m.get('text', '')}\n"
    # Framed as a data-conversion task (not role-play) — otherwise the claude
    # CLI may reject it as "prompt injection" against the Claude Code persona.
    system = (
        "You perform a data-conversion task for a news program. You read a user "
        "message and produce one JSON object in exactly the specified format."
    )
    user = (
        "The context below describes the setup of a personal newspaper. Interpret "
        f"the last user message and produce one JSON object with a friendly reply "
        f"in {target} ('reply') and which changes to make ('actions').\n\n"
        f"SOURCES:\n{src_list}\n\n"
        f"PROFILE: {preferences}\n"
        f"EDITORIAL TOPICS (checkbox keys): {topic_list}\n"
        f"TITLE: {title} · FRONT_PAGE_SIZE: {front_page_size} · "
        f"POLL_MIN: {poll_minutes}\n\n"
        + (f"CONVERSATION SO FAR:\n{transcript}\n" if transcript else "")
        + f"LAST USER MESSAGE:\n{text}\n\n"
        "Produce only this JSON object, with no other text:\n"
        f'{{"reply": "<short reply in {target}>", "actions": [<0 or more actions>]}}\n\n'
        "Valid actions (objects in the actions array):\n"
        '{"action":"add_source","query":"<name or url, e.g. nrk.no>"}\n'
        '{"action":"remove_source","name":"<exact source name from SOURCES>"}\n'
        '{"action":"enable_source","name":"<source name>"}\n'
        '{"action":"disable_source","name":"<source name>"}\n'
        '{"action":"set_preferences","value":"<the whole new profile>"}\n'
        '{"action":"set_title","value":"<title>"}\n'
        '{"action":"set_front_page_size","value":<integer>}\n'
        '{"action":"set_poll_minutes","value":<integer>}\n'
        '{"action":"add_topic","key":"<short-slug>","label":"<short label>","phrase":"<curation phrase in English>"}\n'
        '{"action":"remove_topic","key":"<existing topic key>"}\n\n'
        "Rules: For more/less of an EXISTING topic (see EDITORIAL TOPICS), use "
        "set_preferences with an updated profile that KEEPS what still applies. "
        "When the reader asks for a NEW kind of coverage they don't have a topic "
        "for yet, use add_topic: a short lowercase slug key, a short label in "
        f"{target}, and a concise English curation phrase (e.g. 'space "
        "exploration and astronomy'). Don't duplicate an existing topic key. Use "
        "exact source names from SOURCES. If the message is a question, answer in "
        "'reply' and leave 'actions' as []."
    )
    # One retry — the CLI can occasionally deviate from the format.
    for _ in range(2):
        data = _extract_json(_chat(settings.curate_model, system, user, max_tokens=1000))
        if isinstance(data, dict) and "actions" in data:
            if not isinstance(data["actions"], list):
                data["actions"] = []
            return data
    return None


def suggest_selector(site_url: str, title: str, candidates: list[dict], target: str = "English") -> Optional[dict]:
    """Suggests a CSS selector for article links on a feedless page, based on a
    sample of <a> links (text + class). None without an LLM."""
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
        "You are an expert in HTML/CSS and find robust selectors for article "
        "links on news sites."
    )
    user = (
        f"Website: {site_url}\nPage title: {title}\n\n"
        f"Below are <a> links from the front page with text and class attributes:\n{listing}\n\n"
        "Suggest ONE CSS selector that matches the article/headline links (not "
        "menu, footer, ads or related links). Prefer class-based selectors that "
        f"capture as many of the stories as possible. Also give a short name in "
        f"{target} and a section.\n"
        'Respond ONLY with JSON: {"link_selector": "<css>", "name": "<name>", "section": "<section>"}'
    )
    data = _extract_json(_chat(settings.curate_model, system, user, max_tokens=300))
    if isinstance(data, dict) and data.get("link_selector"):
        return data
    return None


def suggest_feed_url(site_url: str, title: str, target: str = "English") -> Optional[dict]:
    """When no feed was found automatically: ask the LLM for a likely RSS feed
    URL (many are well known, e.g. BBC's feeds.bbci.co.uk). The result is
    validated afterwards. None without an LLM."""
    if not enabled():
        return None
    system = "You know the RSS feeds of major news websites."
    user = (
        f"Website: {site_url}\nPage title: {title}\n\n"
        "Give the most likely RSS feed URL for the main news on this website "
        f"(full URL). Also suggest a short name in {target} and a section. "
        "If you don't know a feed, set url to an empty string.\n"
        'Respond ONLY with JSON: {"url": "<feed-url or empty>", "name": "<name>", "section": "<section>"}'
    )
    data = _extract_json(_chat(settings.curate_model, system, user, max_tokens=300))
    if isinstance(data, dict) and data.get("url"):
        return data
    return None
