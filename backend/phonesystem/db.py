from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


def make_engine(url: str) -> Engine:
    connect_args = {}
    if url.startswith("sqlite"):
        # The provisioning server, syslog receiver and API share one SQLite file
        # from several threads.
        connect_args = {"check_same_thread": False, "timeout": 15}
    engine = create_engine(url, connect_args=connect_args)
    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_conn, _record):  # pragma: no cover - trivial
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA busy_timeout=15000")
            cur.close()

    return engine


class Database:
    """Owns the engine and session factory for one database URL."""

    def __init__(self, url: str):
        self.url = url
        self.engine = make_engine(url)
        self.sessionmaker = sessionmaker(self.engine, expire_on_commit=False)

    @contextmanager
    def session(self) -> Iterator[Session]:
        with self.sessionmaker() as session:
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise
