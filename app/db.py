from sqlmodel import Session, SQLModel, create_engine

from .config import settings

connect_args = (
    {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
)
engine = create_engine(settings.database_url, connect_args=connect_args)


def init_db() -> None:
    # Importer modeller slik at de registreres på metadata før create_all.
    from . import models  # noqa: F401

    SQLModel.metadata.create_all(engine)
    _migrate()


def _migrate() -> None:
    """Legger til kolonner som finnes i modellene, men ikke i en eksisterende
    tabell. SQLModel.create_all lager ikke nye kolonner på gamle tabeller, så
    dette hindrer «no such column» når vi utvider modellene."""
    from sqlalchemy import inspect as sa_inspect, text

    insp = sa_inspect(engine)
    for table in SQLModel.metadata.sorted_tables:
        if not insp.has_table(table.name):
            continue
        existing = {c["name"] for c in insp.get_columns(table.name)}
        for col in table.columns:
            if col.name in existing:
                continue
            coltype = col.type.compile(dialect=engine.dialect)
            try:
                with engine.begin() as conn:
                    conn.execute(
                        text(f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {coltype}')
                    )
                print(f"[db] la til kolonne {table.name}.{col.name}")
            except Exception as e:
                print(f"[db] migrering {table.name}.{col.name} feilet: {e}")


def get_session() -> Session:
    return Session(engine)
