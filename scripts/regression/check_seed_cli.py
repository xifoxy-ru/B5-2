from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import os
import re
import shutil
import sqlite3
import subprocess
import sys

from scripts.regression.assertions import CheckFailure, require
from scripts.regression.context import (
    APPLICATION_DIRECTORY,
    DB_CLI_PATH,
    PROJECT_ROOT,
    SEED_SQL_PATH,
    RegressionContext,
)
from scripts.regression.fixtures import (
    generated_isbn_13,
    reference_isbn_13_checksum_is_valid,
)


def check_seed_cli(context: RegressionContext) -> None:
    product = context.product
    temporary_root = context.temporary_root
    require(product is not None, "application modules are unavailable")
    require(temporary_root is not None, "temporary root is unavailable")
    require(SEED_SQL_PATH.is_file(), "sql/seed.sql is missing")
    require(DB_CLI_PATH.is_file(), "scripts/db_cli.py is missing")

    application_source = "\n".join(
        path.read_text()
        for path in sorted(APPLICATION_DIRECTORY.rglob("*.py"))
    )
    require(
        "INSERT INTO books" not in application_source,
        "Book seed SQL is hardcoded in product Python",
    )
    require(
        "seed_reference_data" not in application_source,
        "legacy Python seed function remains",
    )

    seed_source = SEED_SQL_PATH.read_text()
    require("INSERT INTO books" in seed_source, "seed.sql does not insert Book data")
    require(
        "INSERT OR IGNORE" not in seed_source.upper(),
        "seed.sql uses INSERT OR IGNORE",
    )
    require(
        "INSERT OR REPLACE" not in seed_source.upper(),
        "seed.sql uses INSERT OR REPLACE",
    )
    require("authors" not in seed_source.lower(), "seed.sql references authors")
    require(
        "categories" not in seed_source.lower(),
        "seed.sql references categories",
    )
    require("price_cents" in seed_source, "seed.sql does not use price_cents")

    cli_help = subprocess.run(
        [sys.executable, str(DB_CLI_PATH), "--help"],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    require(
        cli_help.returncode == 0,
        f"db_cli.py --help failed: {cli_help.stderr}",
    )
    require(
        "seed" in cli_help.stdout and "reset" in cli_help.stdout,
        "CLI help omits seed/reset",
    )
    for forbidden_command in (
        "db-init",
        "db-mock",
        "db-clear",
        "db-execute",
    ):
        require(
            forbidden_command not in cli_help.stdout,
            f"CLI exposes {forbidden_command}",
        )

    parser = product.db_cli.build_parser()
    command_action = next(
        action
        for action in parser._actions
        if action.dest == "command"
    )
    require(
        set(command_action.choices) == {"seed", "reset"},
        "CLI subcommands differ",
    )

    original_seed_database = product.db_cli.seed_database
    original_reset_database = product.db_cli.reset_database
    try:
        product.db_cli.seed_database = lambda: 6
        seed_stdout = StringIO()
        with redirect_stdout(seed_stdout):
            seed_exit_code = product.db_cli.main(["seed"])
        require(seed_exit_code == 0, "successful seed CLI exit code differs")
        require(
            "Database seeded successfully." in seed_stdout.getvalue(),
            "seed success message differs",
        )
        require(
            "Inserted 6 books." in seed_stdout.getvalue(),
            "seed inserted count is missing",
        )

        product.db_cli.reset_database = lambda: 6
        reset_stdout = StringIO()
        with redirect_stdout(reset_stdout):
            reset_exit_code = product.db_cli.main(["reset"])
        require(reset_exit_code == 0, "successful reset CLI exit code differs")
        require(
            "Database reset successfully." in reset_stdout.getvalue(),
            "reset success message differs",
        )
        require(
            "Inserted 6 books." in reset_stdout.getvalue(),
            "reset inserted count is missing",
        )

        def fail_seed() -> int:
            raise product.db_cli.DatabaseCLIError(
                "database already contains Book data"
            )

        product.db_cli.seed_database = fail_seed
        seed_stderr = StringIO()
        with redirect_stderr(seed_stderr):
            failed_seed_exit_code = product.db_cli.main(["seed"])
        require(
            failed_seed_exit_code != 0,
            "failed seed CLI returned exit code 0",
        )
        require(
            "Seed failed:" in seed_stderr.getvalue(),
            "seed failure message differs",
        )
    finally:
        product.db_cli.seed_database = original_seed_database
        product.db_cli.reset_database = original_reset_database

    cli_root = temporary_root / "db-cli-check"
    cli_seed_directory = cli_root / "sql"
    cli_seed_directory.mkdir(parents=True)
    cli_seed_path = cli_seed_directory / "seed.sql"
    shutil.copy2(SEED_SQL_PATH, cli_seed_path)
    cli_database_path = cli_root / "library.db"

    inserted_count = product.db_cli.seed_database(
        project_root=cli_root,
        database_path=cli_database_path,
        seed_sql_path=cli_seed_path,
    )
    require(inserted_count == 6, f"seed inserted {inserted_count}, expected 6")

    connection = product.db_cli._connect(cli_database_path)
    try:
        require(
            connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1,
            "DB CLI connection did not enable SQLite Foreign Keys",
        )
        table_names = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' "
                "ORDER BY name"
            )
        ]
        require(
            table_names == ["books"],
            f"seed database tables differ: {table_names!r}",
        )
        columns = [
            row[1]
            for row in connection.execute("PRAGMA table_info(books)")
        ]
        require(
            tuple(columns) == product.db_cli.EXPECTED_BOOK_COLUMNS,
            f"seed database columns differ: {columns!r}",
        )
        require(
            not list(connection.execute("PRAGMA foreign_key_list(books)")),
            "seed database books table has Foreign Keys",
        )
        seed_rows = connection.execute(
            "SELECT book_id, title, author, category, published_year, "
            "price_cents, stock_quantity, isbn "
            "FROM books ORDER BY book_id"
        ).fetchall()
    finally:
        connection.close()

    require(len(seed_rows) == 6, f"seed row count differs: {len(seed_rows)}")
    seed_isbns = [row[7] for row in seed_rows]
    require(
        len(seed_isbns) == len(set(seed_isbns)),
        "seed ISBN values are duplicated",
    )
    require(
        any(isbn.startswith("978") for isbn in seed_isbns),
        "seed has no 978 ISBN",
    )
    require(
        any(isbn.startswith("979") for isbn in seed_isbns),
        "seed has no 979 ISBN",
    )
    for seed_row, isbn in zip(seed_rows, seed_isbns):
        require(
            re.fullmatch(r"\d{13}", isbn) is not None,
            f"seed ISBN is not canonical: {isbn}",
        )
        require(
            reference_isbn_13_checksum_is_valid(isbn),
            f"seed ISBN checksum is invalid: {isbn}",
        )
        require(
            0 < len(seed_row[1]) <= product.policy.MAX_TITLE_LENGTH,
            "seed title length differs",
        )
        require(
            0 < len(seed_row[2]) <= product.policy.MAX_AUTHOR_LENGTH,
            "seed author length differs",
        )
        require(
            0 < len(seed_row[3]) <= product.policy.MAX_CATEGORY_LENGTH,
            "seed category length differs",
        )
        require(
            product.policy.MIN_PUBLISHED_YEAR
            <= seed_row[4]
            <= product.policy.MAX_FUTURE_PUBLICATION_YEAR,
            f"seed publication year is invalid: {seed_row[4]}",
        )
        require(
            product.policy.MIN_STOCK_QUANTITY
            <= seed_row[6]
            <= product.policy.MAX_STOCK_QUANTITY,
            f"seed stock quantity is invalid: {seed_row[6]}",
        )
    require(
        [row[5] for row in seed_rows]
        == [0, 1050, 123456, 4999, 250000, 999999],
        "seed price_cents values differ",
    )
    require(
        any(row[6] == 0 for row in seed_rows),
        "seed has no zero-stock Book",
    )

    rows_before_repeat = seed_rows
    try:
        product.db_cli.seed_database(
            project_root=cli_root,
            database_path=cli_database_path,
            seed_sql_path=cli_seed_path,
        )
    except product.db_cli.DatabaseCLIError as exc:
        require(
            "already contains Book data" in str(exc),
            "repeat seed error message differs",
        )
        require(
            "make db-reset" in str(exc),
            "repeat seed does not recommend reset",
        )
    else:
        raise CheckFailure("repeat seed unexpectedly succeeded")

    connection = sqlite3.connect(cli_database_path)
    try:
        rows_after_repeat = connection.execute(
            "SELECT book_id, title, author, category, published_year, "
            "price_cents, stock_quantity, isbn "
            "FROM books ORDER BY book_id"
        ).fetchall()
        connection.execute(
            "INSERT INTO books "
            "(book_id, title, author, category, published_year, "
            "price_cents, stock_quantity, isbn) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                99,
                "Reset Removal Probe",
                "Probe Author",
                "Probe Category",
                2025,
                1,
                1,
                generated_isbn_13(999),
            ),
        )
        connection.commit()
    finally:
        connection.close()
    require(
        rows_after_repeat == rows_before_repeat,
        "repeat seed changed existing data",
    )

    reset_count = product.db_cli.reset_database(
        project_root=cli_root,
        database_path=cli_database_path,
        seed_sql_path=cli_seed_path,
    )
    require(reset_count == 6, f"reset inserted {reset_count}, expected 6")
    connection = sqlite3.connect(cli_database_path)
    try:
        reset_ids = [
            row[0]
            for row in connection.execute(
                "SELECT book_id FROM books ORDER BY book_id"
            )
        ]
    finally:
        connection.close()
    require(
        reset_ids == [1, 2, 3, 4, 5, 6],
        f"reset Book IDs differ: {reset_ids!r}",
    )

    unsafe_path = cli_root / "other.db"
    try:
        product.db_cli.reset_database(
            project_root=cli_root,
            database_path=unsafe_path,
            seed_sql_path=cli_seed_path,
        )
    except product.db_cli.DatabaseCLIError:
        pass
    else:
        raise CheckFailure("reset accepted a non-library.db path")
    require(not unsafe_path.exists(), "unsafe reset path was created")

    rollback_root = temporary_root / "db-cli-rollback"
    rollback_seed_directory = rollback_root / "sql"
    rollback_seed_directory.mkdir(parents=True)
    rollback_seed_path = rollback_seed_directory / "seed.sql"
    rollback_seed_path.write_text(
        seed_source
        + "\nINSERT INTO books "
        "(book_id, title, author, category, published_year, "
        "price_cents, stock_quantity, isbn) "
        "VALUES "
        "(1, 'Duplicate ID', 'Probe', 'Probe', 2025, 100, 1, "
        "'9786000000073');\n"
    )
    rollback_database_path = rollback_root / "library.db"
    try:
        product.db_cli.seed_database(
            project_root=rollback_root,
            database_path=rollback_database_path,
            seed_sql_path=rollback_seed_path,
        )
    except sqlite3.Error:
        pass
    else:
        raise CheckFailure("broken seed SQL unexpectedly succeeded")
    connection = sqlite3.connect(rollback_database_path)
    try:
        rollback_count = connection.execute(
            "SELECT COUNT(*) FROM books"
        ).fetchone()[0]
    finally:
        connection.close()
    require(
        rollback_count == 0,
        f"failed seed left {rollback_count} partial rows",
    )
