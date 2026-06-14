"""Interaktiv oppsett-veiviser. Kjøres med `./avisa setup` (eller
`python -m app.setup_wizard`). Stiller spørsmål om avistittel, redaksjonell
profil og kilder, og skriver til databasen — i samme ånd som openpapers
onboarding, men for den alltid-på tjenesten."""

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
    d = "J/n" if default_yes else "j/N"
    ans = input(f"{prompt} ({d}): ").strip().lower()
    if not ans:
        return default_yes
    return ans in ("j", "ja", "y", "yes")


def _add_by_discovery(query: str) -> None:
    """Smart oppsett: finn ut av en bar URL/domene."""
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
        print(f"    ✓ {prop['name']} — {prop['kind'].upper()} i {prop['section']} "
              f"({prop['entries']} saker)")
    else:
        print(f"    ⚠ {prop.get('reason', 'klarte ikke å finne ut av den')}")


def _add_manual() -> None:
    print("\n  Manuell kilde (blank Navn for å avslutte):")
    name = input("    Navn: ").strip()
    if not name:
        return "stop"
    kind = _ask("    Type (rss/api/playwright)", "rss")
    url = _ask("    URL")
    section = _ask("    Seksjon", "Nyheter")
    cfg = None
    if kind == "playwright":
        sel = _ask("    CSS-selector for artikkel-lenker (link_selector)")
        if sel:
            cfg = json.dumps({"link_selector": sel})
    with get_session() as s:
        s.add(Source(name=name, kind=kind, url=url, section=section, enabled=True, config=cfg))
        s.commit()
    print(f"    ✓ La til {name}")


def main() -> None:
    print("=" * 56)
    print("  Avisa — oppsett")
    print("=" * 56)
    init_db()

    print(f"\nLLM-provider: {llm.provider_label()}")
    if not llm.enabled():
        print("  (Tips: sett OPENROUTER_API_KEY i .env, eller logg inn med "
              "`claude` lokalt, for kuratering + oversettelse.)")

    # Tittel + profil
    print("\n— Avis —")
    title = _ask("Tittel på avisa", runtime_config.paper_title())
    runtime_config.set_value("paper_title", title)

    print("\n— Redaksjonell profil —")
    print("Beskriv hva du vil lese (temaer, vekting, hva du vil unngå).")
    print(f"Nåværende: {runtime_config.preferences()}")
    if _ask_yes("Vil du endre profilen?", default_yes=False):
        prof = input("Ny profil:\n  ").strip()
        if prof:
            runtime_config.set_value("preferences", prof)

    size = _ask("Antall saker på forsiden", str(runtime_config.front_page_size()))
    runtime_config.set_value("front_page_size", size)
    poll = _ask("Poll-intervall i minutter", str(runtime_config.poll_minutes()))
    runtime_config.set_value("poll_minutes", poll)

    # Kilder
    print("\n— Kilder —")
    with get_session() as s:
        existing = s.exec(select(Source)).all()
    if existing:
        print(f"Du har allerede {len(existing)} kilder:")
        for src in existing:
            print(f"  · {src.name} ({src.kind}) — {'på' if src.enabled else 'av'}")
    if _ask_yes("Vil du legge til kilder nå?", default_yes=not existing):
        print("Skriv nettsteder du leser — bare navn/URL, komma-separert.")
        print("  f.eks.: nrk.no, bbc.com, aftenposten.no")
        line = input("Nettsteder: ").strip()
        for q in [x.strip() for x in line.split(",") if x.strip()]:
            print(f"  Sjekker {q} …")
            _add_by_discovery(q)
        if _ask_yes("Vil du legge til noen manuelt (API/playwright)?", default_yes=False):
            while True:
                if _add_manual() == "stop":
                    break

    print("\n✓ Oppsett ferdig. Start med:  ./avisa start")


if __name__ == "__main__":
    main()
