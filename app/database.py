from collections.abc import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

DATABASE_URL = "sqlite:///./library.db"

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
)


def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
    finally:
        cursor.close()


if engine.dialect.name == "sqlite" and not event.contains(
    engine,
    "connect",
    _enable_sqlite_foreign_keys,
):
    event.listen(engine, "connect", _enable_sqlite_foreign_keys)


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    import app.models.book

    Base.metadata.create_all(bind=engine)
