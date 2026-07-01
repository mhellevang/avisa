from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine

from .config import settings

_is_sqlite = settings.database_url.startswith("sqlite")
connect_args = {"check_same_thread": False} if _is_sqlite else {}
engine = create_engine(settings.database_url, connect_args=connect_args)

if _is_sqlite:

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record):
        # The pipeline thread and the web workers write concurrently. In the
        # default rollback-journal mode a long pipeline commit blocks readers
        # and quickly yields "database is locked" in web requests; WAL lets
        # readers run during a write, and busy_timeout makes writers wait
        # instead of erroring.
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=15000")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.close()


def init_db() -> None:
    # Import models so they get registered on the metadata before create_all.
    from . import models  # noqa: F401

    SQLModel.metadata.create_all(engine)
    _migrate()


def _migrate() -> None:
    """Adds columns that exist in the models but not in an existing table.
    SQLModel.create_all does not create new columns on old tables, so this
    prevents "no such column" when we extend the models."""
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
                print(f"[db] added column {table.name}.{col.name}")
            except Exception as e:
                print(f"[db] migration {table.name}.{col.name} failed: {e}")


def get_session() -> Session:
    return Session(engine)
