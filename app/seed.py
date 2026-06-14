import json
from pathlib import Path

import yaml
from sqlmodel import select

from .db import get_session
from .models import Source

SOURCES_FILE = Path(__file__).resolve().parent.parent / "sources.yaml"


def seed_sources() -> int:
    """Leser sources.yaml inn i DB hvis tabellen er tom. Returnerer antall
    nye kilder."""
    if not SOURCES_FILE.exists():
        print(f"[seed] fant ikke {SOURCES_FILE}")
        return 0

    with get_session() as s:
        existing = s.exec(select(Source)).first()
        if existing:
            return 0

        data = yaml.safe_load(SOURCES_FILE.read_text(encoding="utf-8")) or []
        count = 0
        for row in data:
            cfg = row.get("config")
            s.add(
                Source(
                    name=row["name"],
                    kind=row["kind"],
                    url=row["url"],
                    section=row.get("section", "Nyheter"),
                    lang=row.get("lang", "en"),
                    enabled=row.get("enabled", True),
                    config=json.dumps(cfg) if cfg else None,
                )
            )
            count += 1
        s.commit()
    print(f"[seed] la inn {count} kilder")
    return count
