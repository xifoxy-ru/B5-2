from __future__ import annotations

from dataclasses import dataclass
import importlib
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import Any

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from scripts.regression.assertions import require
from scripts.regression.client import DirectASGIClient


PROJECT_ROOT = Path(__file__).resolve().parents[2]
APPLICATION_DIRECTORY = PROJECT_ROOT / "app"
LIBRARY_DATABASE_PATH = PROJECT_ROOT / "library.db"
SEED_SQL_PATH = PROJECT_ROOT / "sql" / "seed.sql"
DB_CLI_PATH = PROJECT_ROOT / "scripts" / "db_cli.py"
MAKEFILE_PATH = PROJECT_ROOT / "Makefile"


@dataclass(frozen=True)
class FileSnapshot:
    exists: bool
    size: int | None
    modified_time_ns: int | None


def snapshot_file(path: Path) -> FileSnapshot:
    if not path.exists():
        return FileSnapshot(False, None, None)
    file_stat = path.stat()
    return FileSnapshot(True, file_stat.st_size, file_stat.st_mtime_ns)


def check_python_compile() -> None:
    source_paths = sorted(APPLICATION_DIRECTORY.rglob("*.py"))
    source_paths.extend(sorted((PROJECT_ROOT / "scripts" / "regression").rglob("*.py")))
    source_paths.extend(
        [
            PROJECT_ROOT / "scripts" / "regression_check.py",
            DB_CLI_PATH,
        ]
    )
    for source_path in source_paths:
        compile(source_path.read_bytes(), str(source_path), "exec")


class RegressionContext:
    def __init__(self) -> None:
        self.initial_working_directory = Path.cwd()
        self.library_before = snapshot_file(LIBRARY_DATABASE_PATH)
        self.temporary_directory: TemporaryDirectory[str] | None = None
        self.temporary_root: Path | None = None
        self.temporary_database_path: Path | None = None
        self.temporary_engine: Any = None
        self.product: SimpleNamespace | None = None
        self.client: DirectASGIClient | None = None
        self.original_engine: Any = None
        self.original_session_local: Any = None
        self.original_dont_write_bytecode = sys.dont_write_bytecode

    def __enter__(self) -> RegressionContext:
        sys.dont_write_bytecode = True
        self.temporary_directory = TemporaryDirectory(prefix="b5-2-regression-")
        self.temporary_root = Path(self.temporary_directory.name)
        return self

    def __exit__(self, _exc_type: Any, _exc: Any, _traceback: Any) -> None:
        if self.product is not None and self.original_engine is not None:
            self.product.database.engine = self.original_engine
            self.product.database.SessionLocal = self.original_session_local
        if self.temporary_engine is not None:
            self.temporary_engine.dispose()
        os.chdir(self.initial_working_directory)
        if self.temporary_directory is not None:
            self.temporary_directory.cleanup()
        sys.dont_write_bytecode = self.original_dont_write_bytecode

    def import_application(self) -> SimpleNamespace:
        require(self.temporary_root is not None, "temporary root is unavailable")
        os.chdir(self.temporary_root)
        try:
            database = importlib.import_module("app.database")
            main = importlib.import_module("app.main")
            book_router_module = importlib.import_module("app.routers.book_router")
            home_router_module = importlib.import_module("app.routers.home_router")
            central_router_module = importlib.import_module("app.routers.router")
            schemas = importlib.import_module("app.schemas.book")
            book_service = importlib.import_module("app.services.book_service")
            policy = importlib.import_module("app.services.book_validation_policy")
            template_config = importlib.import_module("app.template_config")
            book_model = importlib.import_module("app.models.book")
            db_cli = importlib.import_module("scripts.db_cli")
        finally:
            os.chdir(PROJECT_ROOT)

        require(main.app.title == "B5-2 Book CRUD", "unexpected application title")
        self.product = SimpleNamespace(
            app=main.app,
            database=database,
            book_router_module=book_router_module,
            home_router_module=home_router_module,
            central_router_module=central_router_module,
            BookFormData=schemas.BookFormData,
            book_service=book_service,
            policy=policy,
            template_config=template_config,
            Book=book_model.Book,
            db_cli=db_cli,
        )
        self.client = DirectASGIClient(main.app)
        self._install_database_override()
        return self.product

    def _install_database_override(self) -> None:
        require(self.product is not None, "application modules are unavailable")
        require(self.temporary_root is not None, "temporary root is unavailable")
        self.original_engine = self.product.database.engine
        self.original_session_local = self.product.database.SessionLocal
        self.temporary_database_path = self.temporary_root / "regression.db"
        self.temporary_engine = create_engine(
            f"sqlite:///{self.temporary_database_path}",
            connect_args={"check_same_thread": False},
        )
        event.listen(
            self.temporary_engine,
            "connect",
            self.product.database._enable_sqlite_foreign_keys,
        )
        self.product.database.engine = self.temporary_engine
        self.product.database.SessionLocal = sessionmaker(
            autocommit=False,
            autoflush=False,
            bind=self.temporary_engine,
        )

    def initialize_database(self) -> None:
        require(self.product is not None, "application modules are unavailable")
        self.product.database.init_db()

    def reset_database(self) -> None:
        require(self.product is not None, "application modules are unavailable")
        require(self.temporary_engine is not None, "temporary Engine is unavailable")
        self.product.database.Base.metadata.drop_all(bind=self.temporary_engine)
        self.product.database.init_db()

    def library_is_unchanged(self) -> bool:
        return snapshot_file(LIBRARY_DATABASE_PATH) == self.library_before

    def temporary_artifacts_are_cleaned(self) -> bool:
        return self.temporary_root is None or not self.temporary_root.exists()
