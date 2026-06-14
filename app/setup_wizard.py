"""Interactive setup wizard. Run with `./avisa setup` (or
`python -m app.setup_wizard`). Asks about the paper title, editorial
profile, and sources, and writes to the database — in the same spirit as
openpaper's onboarding, but for the always-on service."""

import json

from sqlmodel import select

from . import llm, runtime_config
from .db import get_session, init_db
from .fetchers import discover
from .models import Source


def _ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    ans = input(f"{prompt}{suffix}: ").strip()
    return ans or default


def _ask_yes(prompt: str, default_yes: bool = True) -> bool:
    d = "Y/n" if default_yes else "y/N"
    ans = input(f"{prompt} ({d}): ").strip().lower()
    if not ans:
        return default_yes
    return ans in ("y", "yes")


def _add_by_discovery(query: str) -> None:
    """Smart setup: figure out a bare URL/domain."""
    prop = discover.propose(query)
    if prop.get("ok"):
        with get_session() as s:
            s.add(
                Source(
                    name=prop["name"],
                    kind=prop["kind"],
                    url=prop["url"],
                    section=prop["section"],
                    enabled=True,
                    config=prop.get("config"),
                )
            )
            s.commit()
        print(f"    ✓ {prop['name']} — {prop['kind'].upper()} in {prop['section']} "
              f"({prop['entries']} stories)")
    else:
        print(f"    ⚠ {prop.get('reason', 'could not figure it out')}")


def _add_manual() -> None:
    print("\n  Manual source (blank Name to finish):")
    name = input("    Name: ").strip()
    if not name:
        return "stop"
    kind = _ask("    Type (rss/api/playwright)", "rss")
    url = _ask("    URL")
    section = _ask("    Section", "News")
    cfg = None
    if kind == "playwright":
        sel = _ask("    CSS selector for article links (link_selector)")
        if sel:
            cfg = json.dumps({"link_selector": sel})
    with get_session() as s:
        s.add(Source(name=name, kind=kind, url=url, section=section, enabled=True, config=cfg))
        s.commit()
    print(f"    ✓ Added {name}")


def main() -> None:
    print("=" * 56)
    print("  Avisa — setup")
    print("=" * 56)
    init_db()

    print(f"\nLLM provider: {llm.provider_label()}")
    if not llm.enabled():
        print("  (Tip: set OPENROUTER_API_KEY in .env, or log in with "
              "`claude` locally, for curation + translation.)")

    # Title + profile
    print("\n— Paper —")
    title = _ask("Paper title", runtime_config.paper_title())
    runtime_config.set_value("paper_title", title)

    print("\n— Editorial profile —")
    print("Describe what you want to read (topics, weighting, what to avoid).")
    print(f"Current: {runtime_config.preferences()}")
    if _ask_yes("Do you want to change the profile?", default_yes=False):
        prof = input("New profile:\n  ").strip()
        if prof:
            runtime_config.set_value("preferences", prof)

    size = _ask("Number of stories on the front page", str(runtime_config.front_page_size()))
    runtime_config.set_value("front_page_size", size)
    poll = _ask("Poll interval in minutes", str(runtime_config.poll_minutes()))
    runtime_config.set_value("poll_minutes", poll)

    # Sources
    print("\n— Sources —")
    with get_session() as s:
        existing = s.exec(select(Source)).all()
    if existing:
        print(f"You already have {len(existing)} sources:")
        for src in existing:
            print(f"  · {src.name} ({src.kind}) — {'on' if src.enabled else 'off'}")
    if _ask_yes("Do you want to add sources now?", default_yes=not existing):
        print("Enter the sites you read — just name/URL, comma-separated.")
        print("  e.g.: nrk.no, bbc.com, aftenposten.no")
        line = input("Sites: ").strip()
        for q in [x.strip() for x in line.split(",") if x.strip()]:
            print(f"  Checking {q} …")
            _add_by_discovery(q)
        if _ask_yes("Do you want to add any manually (API/playwright)?", default_yes=False):
            while True:
                if _add_manual() == "stop":
                    break

    print("\n✓ Setup complete. Start with:  ./avisa start")


if __name__ == "__main__":
    main()
