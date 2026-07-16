from __future__ import annotations

import argparse
from pathlib import Path
import sqlite3
import sys

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATABASE_PATH = PROJECT_ROOT / "library.db"
SEED_SQL_PATH = PROJECT_ROOT / "sql" / "seed.sql"
EXPECTED_BOOK_COLUMNS = (
    "book_id",
    "title",
    "author",
    "category",
    "published_year",
    "price_cents",
    "stock_quantity",
    "isbn",
)

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.database import Base, _enable_sqlite_foreign_keys
import app.models.book


class DatabaseCLIError(RuntimeError):
    pass


def _validate_paths(
    project_root: Path,
    database_path: Path,
    seed_sql_path: Path,
) -> tuple[Path, Path, Path]:
    root = project_root.resolve()
    expected_database_path = root / "library.db"
    expected_seed_sql_path = root / "sql" / "seed.sql"
    candidate_database_path = database_path.parent.resolve() / database_path.name
    candidate_seed_sql_path = seed_sql_path.parent.resolve() / seed_sql_path.name

    if candidate_database_path != expected_database_path:
        raise DatabaseCLIError(
            f"database path must be the project library.db: {expected_database_path}"
        )
    if candidate_database_path.is_symlink():
        raise DatabaseCLIError("database path must not be a symbolic link")
    if candidate_seed_sql_path != expected_seed_sql_path:
        raise DatabaseCLIError(
            f"seed SQL path must be the project sql/seed.sql: {expected_seed_sql_path}"
        )
    if candidate_seed_sql_path.is_symlink():
        raise DatabaseCLIError("seed SQL path must not be a symbolic link")

    return root, candidate_database_path, candidate_seed_sql_path


def _create_engine(database_path: Path) -> Engine:
    database_engine = create_engine(
        f"sqlite:///{database_path}",
        connect_args={"check_same_thread": False},
    )
    event.listen(
        database_engine,
        "connect",
        _enable_sqlite_foreign_keys,
    )
    return database_engine


def _initialize_database(database_path: Path) -> None:
    database_engine = _create_engine(database_path)
    try:
        Base.metadata.create_all(bind=database_engine)
    finally:
        database_engine.dispose()


def _connect(database_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(database_path)
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def _validate_schema(connection: sqlite3.Connection) -> None:
    table_names = [
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' "
            "ORDER BY name"
        )
    ]
    if table_names != ["books"]:
        raise DatabaseCLIError(f"expected only the books table, found: {table_names}")

    columns = [
        row[1]
        for row in connection.execute("PRAGMA table_info(books)")
    ]
    if tuple(columns) != EXPECTED_BOOK_COLUMNS:
        raise DatabaseCLIError(f"books table columns differ: {columns}")

    foreign_keys = list(connection.execute("PRAGMA foreign_key_list(books)"))
    if foreign_keys:
        raise DatabaseCLIError("books table must not contain Foreign Keys")


def _read_seed_sql(seed_sql_path: Path) -> str:
    if not seed_sql_path.is_file():
        raise DatabaseCLIError(f"seed SQL file does not exist: {seed_sql_path}")

    seed_sql = seed_sql_path.read_text(encoding="utf-8").strip()
    if not seed_sql:
        raise DatabaseCLIError("seed SQL file is empty")
    return seed_sql


def seed_database(
    *,
    project_root: Path = PROJECT_ROOT,
    database_path: Path = DATABASE_PATH,
    seed_sql_path: Path = SEED_SQL_PATH,
) -> int:
    _, validated_database_path, validated_seed_sql_path = _validate_paths(
        project_root,
        database_path,
        seed_sql_path,
    )
    seed_sql = _read_seed_sql(validated_seed_sql_path)

    _initialize_database(validated_database_path)

    connection = _connect(validated_database_path)
    try:
        _validate_schema(connection)
        existing_count = connection.execute(
            "SELECT COUNT(*) FROM books"
        ).fetchone()[0]
        if existing_count != 0:
            raise DatabaseCLIError(
                "database already contains Book data.\n"
                "Run `make db-reset` to recreate the database."
            )

        try:
            connection.executescript(f"BEGIN IMMEDIATE;\n{seed_sql}\n")
            inserted_count = connection.execute(
                "SELECT COUNT(*) FROM books"
            ).fetchone()[0]
            if inserted_count <= 0:
                raise DatabaseCLIError("seed SQL did not insert any Book data")
            connection.commit()
        except (sqlite3.Error, DatabaseCLIError):
            if connection.in_transaction:
                connection.rollback()
            raise

        return inserted_count
    finally:
        connection.close()


def reset_database(
    *,
    project_root: Path = PROJECT_ROOT,
    database_path: Path = DATABASE_PATH,
    seed_sql_path: Path = SEED_SQL_PATH,
) -> int:
    _, validated_database_path, validated_seed_sql_path = _validate_paths(
        project_root,
        database_path,
        seed_sql_path,
    )
    _read_seed_sql(validated_seed_sql_path)

    if validated_database_path.exists():
        validated_database_path.unlink()

    _initialize_database(validated_database_path)
    return seed_database(
        project_root=project_root,
        database_path=validated_database_path,
        seed_sql_path=validated_seed_sql_path,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Manage the project library.db seed data.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("seed", help="Seed an empty books table.")
    subparsers.add_parser("reset", help="Recreate library.db and apply seed data.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    action_label = "Seed" if args.command == "seed" else "Reset"

    try:
        if args.command == "seed":
            inserted_count = seed_database()
            print("Database seeded successfully.")
        else:
            inserted_count = reset_database()
            print("Database reset successfully.")
    except (DatabaseCLIError, OSError, sqlite3.Error, SQLAlchemyError) as exc:
        print(f"{action_label} failed: {exc}", file=sys.stderr)
        return 1

    print(f"Inserted {inserted_count} books.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
