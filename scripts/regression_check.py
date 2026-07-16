from __future__ import annotations

import asyncio
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass, fields
from decimal import Decimal
from html import escape
from html.parser import HTMLParser
from io import StringIO
import importlib
import inspect
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import Any, Callable, Coroutine
from urllib.parse import urlencode, urlsplit

from sqlalchemy import BigInteger, String, create_engine, event, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker


PROJECT_ROOT = Path(__file__).resolve().parents[1]
APPLICATION_DIRECTORY = PROJECT_ROOT / "app"
LIBRARY_DATABASE_PATH = PROJECT_ROOT / "library.db"
SEED_SQL_PATH = PROJECT_ROOT / "sql" / "seed.sql"
DB_CLI_PATH = PROJECT_ROOT / "scripts" / "db_cli.py"
MAKEFILE_PATH = PROJECT_ROOT / "Makefile"

sys.dont_write_bytecode = True
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


class CheckFailure(AssertionError):
    pass


class StopRegressionChecks(Exception):
    pass


@dataclass(frozen=True)
class FileSnapshot:
    exists: bool
    size: int | None
    modified_time_ns: int | None


@dataclass(frozen=True)
class ASGIResponse:
    status_code: int
    headers: dict[str, str]
    body: str


class HTMLTargets(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[str] = []
        self.form_actions: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "a" and attributes.get("href") is not None:
            self.links.append(attributes["href"] or "")
        if tag == "form" and attributes.get("action") is not None:
            self.form_actions.append(attributes["action"] or "")


class DirectASGIClient:
    def __init__(self, app: Any) -> None:
        self.app = app

    async def request(
        self,
        method: str,
        path: str,
        *,
        query: str = "",
        form: dict[str, str] | None = None,
    ) -> ASGIResponse:
        body = urlencode(form).encode() if form is not None else b""
        headers = [(b"host", b"testserver")]
        if form is not None:
            headers.extend(
                [
                    (b"content-type", b"application/x-www-form-urlencoded"),
                    (b"content-length", str(len(body)).encode()),
                ]
            )

        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "scheme": "http",
            "method": method,
            "path": path,
            "raw_path": path.encode(),
            "query_string": query.encode(),
            "root_path": "",
            "headers": headers,
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 80),
        }
        messages: list[dict[str, Any]] = []
        request_sent = False

        async def receive() -> dict[str, Any]:
            nonlocal request_sent
            if not request_sent:
                request_sent = True
                return {"type": "http.request", "body": body, "more_body": False}
            return {"type": "http.disconnect"}

        async def send(message: dict[str, Any]) -> None:
            messages.append(message)

        await self.app(scope, receive, send)

        start_message = next(
            (message for message in messages if message["type"] == "http.response.start"),
            None,
        )
        require(start_message is not None, f"{method} {path} did not start a response")
        response_body = b"".join(
            message.get("body", b"")
            for message in messages
            if message["type"] == "http.response.body"
        )
        response_headers = {
            key.decode().lower(): value.decode()
            for key, value in start_message["headers"]
        }
        return ASGIResponse(
            status_code=start_message["status"],
            headers=response_headers,
            body=response_body.decode(),
        )


def require(condition: bool, message: str) -> None:
    if not condition:
        raise CheckFailure(message)


def snapshot_file(path: Path) -> FileSnapshot:
    if not path.exists():
        return FileSnapshot(False, None, None)
    file_stat = path.stat()
    return FileSnapshot(True, file_stat.st_size, file_stat.st_mtime_ns)


def run_check(name: str, operation: Callable[[], Any]) -> Any:
    try:
        result = operation()
    except Exception as exc:
        detail = str(exc) or type(exc).__name__
        print(f"[FAIL] {name}: {detail}", file=sys.stderr)
        raise StopRegressionChecks from exc
    print(f"[PASS] {name}")
    return result


async def run_async_check(
    name: str,
    operation: Callable[[], Coroutine[Any, Any, Any]],
) -> Any:
    try:
        result = await operation()
    except Exception as exc:
        detail = str(exc) or type(exc).__name__
        print(f"[FAIL] {name}: {detail}", file=sys.stderr)
        raise StopRegressionChecks from exc
    print(f"[PASS] {name}")
    return result


def check_python_compile() -> None:
    source_paths = sorted(APPLICATION_DIRECTORY.rglob("*.py"))
    source_paths.append(Path(__file__).resolve())
    source_paths.append(DB_CLI_PATH)
    for source_path in source_paths:
        compile(source_path.read_bytes(), str(source_path), "exec")


def import_application(temporary_root: Path) -> SimpleNamespace:
    os.chdir(temporary_root)
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
    return SimpleNamespace(
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


def reference_isbn_10_checksum_is_valid(isbn: str) -> bool:
    digits = [int(character) for character in isbn[:9]]
    check_digit = 10 if isbn[9] == "X" else int(isbn[9])
    return (
        sum(
            weight * digit
            for weight, digit in zip(range(10, 1, -1), digits)
        )
        + check_digit
    ) % 11 == 0


def reference_isbn_13_check_digit(first_twelve_digits: str) -> str:
    weighted_sum = sum(
        int(character) * (1 if index % 2 == 0 else 3)
        for index, character in enumerate(first_twelve_digits)
    )
    return str((10 - weighted_sum % 10) % 10)


def reference_isbn_13_checksum_is_valid(isbn: str) -> bool:
    return isbn[-1] == reference_isbn_13_check_digit(isbn[:12])


def check_isbn_policy(product: SimpleNamespace) -> None:
    valid_cases = [
        ("numeric ISBN-10", "0306406152", "9780306406157"),
        ("hyphenated ISBN-10", "0-306-40615-2", "9780306406157"),
        ("X ISBN-10", "080442957X", "9780804429573"),
        ("lowercase x ISBN-10", "0-8044-2957-x", "9780804429573"),
        ("978 ISBN-13", "9780306406157", "9780306406157"),
        ("hyphenated ISBN-13", "978-0-306-40615-7", "9780306406157"),
        ("surrounding whitespace", "  9780306406157  ", "9780306406157"),
        ("979 ISBN-13", "9791234567896", "9791234567896"),
        ("978978 prefix sequence", "9789781234569", "9789781234569"),
        ("978979 prefix sequence", "9789791234566", "9789791234566"),
        ("979978 prefix sequence", "9799781234568", "9799781234568"),
        ("979979 prefix sequence", "9799791234565", "9799791234565"),
    ]
    for label, raw_isbn, expected_isbn in valid_cases:
        compact_isbn = raw_isbn.strip().replace("-", "").replace("x", "X")
        if len(compact_isbn) == 10:
            require(
                reference_isbn_10_checksum_is_valid(compact_isbn),
                f"{label}: test ISBN-10 checksum is not valid",
            )
            first_twelve_digits = f"978{compact_isbn[:9]}"
            reference_canonical = (
                first_twelve_digits
                + reference_isbn_13_check_digit(first_twelve_digits)
            )
            require(
                expected_isbn == reference_canonical,
                f"{label}: expected conversion was not independently calculated",
            )
        else:
            require(
                compact_isbn.isdigit()
                and compact_isbn.startswith(("978", "979"))
                and reference_isbn_13_checksum_is_valid(compact_isbn),
                f"{label}: test ISBN-13 is not valid",
            )

        canonical_isbn = product.policy.canonicalize_isbn(raw_isbn)
        require(canonical_isbn == expected_isbn, f"{label}: canonical ISBN differs")
        require(
            re.fullmatch(r"\d{13}", canonical_isbn) is not None,
            f"{label}: canonical ISBN is not 13 digits",
        )

    require(
        reference_isbn_13_checksum_is_valid("9770306406158"),
        "invalid-prefix test ISBN does not have a valid checksum",
    )
    invalid_cases = [
        ("ISBN invalid character", "030640615A", "characters that are not allowed"),
        ("ISBN-10 X in first nine", "03064X6152", "ISBN-10 must use digits"),
        ("ISBN-10 invalid checksum", "0306406153", "ISBN-10 check digit is invalid"),
        ("ISBN-13 non-digit", "97803064061X7", "ISBN-13 must contain digits only"),
        ("ISBN-13 invalid prefix", "9770306406158", "ISBN-13 must start with 978 or 979"),
        ("ISBN-13 invalid checksum", "9780306406158", "ISBN-13 check digit is invalid"),
        ("ISBN invalid length", "03064061520", "10 or 13 characters"),
        ("ISBN start hyphen", "-0306406152", "must not start with a hyphen"),
        ("ISBN end hyphen", "0306406152-", "must not end with a hyphen"),
        ("ISBN consecutive hyphens", "0--306406152", "consecutive hyphens"),
        ("ISBN internal whitespace", "03064 06152", "must not contain whitespace"),
    ]
    for label, raw_isbn, expected_message in invalid_cases:
        try:
            product.policy.canonicalize_isbn(raw_isbn)
        except product.policy.ISBNValidationError as exc:
            require(expected_message in str(exc), f"{label}: unexpected error {exc!s}")
        else:
            raise CheckFailure(f"{label}: invalid ISBN was accepted")


def check_price_policy(product: SimpleNamespace) -> None:
    valid_cases = {
        "0": 0,
        "00": 0,
        "000": 0,
        "0.00": 0,
        "0.01": 1,
        "1": 100,
        "10": 1000,
        "10.0": 1000,
        "10.00": 1000,
        "10.5": 1050,
        "00010": 1000,
        "00010.50": 1050,
        "1234.56": 123456,
        "999999999999999": 99_999_999_999_999_900,
        "999999999999999.9": 99_999_999_999_999_990,
        "999999999999999.99": 99_999_999_999_999_999,
    }
    for raw_price, expected_cents in valid_cases.items():
        errors: dict[str, str] = {}
        actual_cents = product.book_service._parse_required_price_cents(raw_price, errors)
        require(not errors, f"{raw_price}: valid price produced errors {errors!r}")
        require(actual_cents == expected_cents, f"{raw_price}: cents differ: {actual_cents!r}")
        require(isinstance(actual_cents, int), f"{raw_price}: cents is not int")
        restored = product.book_service.price_cents_to_decimal(actual_cents)
        require(restored == Decimal(expected_cents) / Decimal("100"), f"{raw_price}: Decimal restore differs")
        require(
            product.book_service.price_cents_to_form_value(actual_cents)
            == format(Decimal(expected_cents) / Decimal("100"), ".2f"),
            f"{raw_price}: Form value restore differs",
        )

    invalid_cases = [
        ("", "This field is required."),
        ("   ", "This field is required."),
        ("-0", "Price must be 0 or greater."),
        ("-0.00", "Price must be 0 or greater."),
        ("-1", "Price must be 0 or greater."),
        ("-0.01", "Price must be 0 or greater."),
        ("+10", "Price must be a decimal amount"),
        ("abc", "Price must be a decimal amount"),
        ("10a", "Price must be a decimal amount"),
        ("$10", "Price must be a decimal amount"),
        ("1,000", "Price must be a decimal amount"),
        ("10.", "Price must be a decimal amount"),
        (".50", "Price must be a decimal amount"),
        ("10.000", "Price may have at most 2 decimal places."),
        ("10.123", "Price may have at most 2 decimal places."),
        ("1e3", "Price must be a decimal amount"),
        ("1E3", "Price must be a decimal amount"),
        ("NaN", "Price must be a finite number."),
        ("Infinity", "Price must be a finite number."),
        ("-Infinity", "Price must be a finite number."),
        ("1000000000000000", "Price must be at most"),
        ("1000000000000000.00", "Price must be at most"),
        ("999999999999999.999", "Price may have at most 2 decimal places."),
    ]
    for raw_price, expected_message in invalid_cases:
        errors = {}
        actual_cents = product.book_service._parse_required_price_cents(raw_price, errors)
        require(actual_cents is None, f"{raw_price!r}: invalid price produced cents")
        require(expected_message in errors.get("price", ""), f"{raw_price!r}: unexpected price error {errors!r}")


def check_price_display(product: SimpleNamespace) -> None:
    display_cases = {
        0: "$0.00",
        1: "$0.01",
        100: "$1.00",
        1050: "$10.50",
        123456: "$1,234.56",
        99_999_999_999_999_999: "$999,999,999,999,999.99",
    }
    for price_cents, expected_display in display_cases.items():
        actual_display = product.template_config.format_price(price_cents)
        require(
            actual_display == expected_display,
            f"{price_cents} cents display differs: {actual_display!r}",
        )
    require(product.template_config.format_price(None) == "$0.00", "price display fallback differs")


def check_route_and_phase_contracts(product: SimpleNamespace, original_engine: Any) -> None:
    expected_routes = [
        ("home", "/", frozenset({"GET"})),
        ("book_list", "/books", frozenset({"GET"})),
        ("book_new", "/books/new", frozenset({"GET"})),
        ("book_create", "/books", frozenset({"POST"})),
        ("book_detail", "/books/{book_id}", frozenset({"GET"})),
        ("book_edit", "/books/{book_id}/edit", frozenset({"GET"})),
        ("book_update", "/books/{book_id}/edit", frozenset({"POST"})),
        ("book_delete", "/books/{book_id}/delete", frozenset({"POST"})),
    ]
    home_router = product.home_router_module.router
    book_router = product.book_router_module.router
    central_router = product.central_router_module.router
    actual_routes = [
        (route.name, route.path, frozenset(route.methods or set()))
        for route in [*home_router.routes, *book_router.routes]
    ]

    require(actual_routes == expected_routes, f"route order/contract differs: {actual_routes!r}")
    route_names = [route_name for route_name, _, _ in actual_routes]
    require(len(route_names) == len(set(route_names)), "business route names are duplicated")
    require(all("/books/books" not in path for _, path, _ in actual_routes), "double /books prefix found")
    require(book_router.prefix == "/books", "Book Router prefix is not /books")

    included_routers = [getattr(route, "original_router", None) for route in central_router.routes]
    require(included_routers == [home_router, book_router], "central Router assembly/order differs")
    app_includes = [getattr(route, "original_router", None) for route in product.app.routes]
    require(central_router in app_includes, "application does not include the central Router")

    expected_urls = {
        "home": "/",
        "book_list": "/books",
        "book_new": "/books/new",
        "book_create": "/books",
        "book_detail": "/books/7",
        "book_edit": "/books/7/edit",
        "book_update": "/books/7/edit",
        "book_delete": "/books/7/delete",
    }
    for route_name, expected_url in expected_urls.items():
        path_parameters = {"book_id": "7"} if "{book_id}" in next(
            path for name, path, _ in expected_routes if name == route_name
        ) else {}
        actual_url = str(product.app.url_path_for(route_name, **path_parameters))
        require(actual_url == expected_url, f"{route_name} generated {actual_url}, expected {expected_url}")

    dto_fields = [field.name for field in fields(product.BookFormData)]
    require(
        dto_fields
        == [
            "title",
            "author",
            "category",
            "published_year",
            "price",
            "stock_quantity",
            "isbn",
        ],
        f"BookFormData fields differ: {dto_fields!r}",
    )
    require(
        inspect.signature(product.book_router_module._book_form_data).return_annotation
        is product.BookFormData,
        "Router form dependency does not return BookFormData",
    )
    for service_name in ("create_book", "update_book"):
        annotation = inspect.signature(getattr(product.book_service, service_name)).parameters[
            "form_data"
        ].annotation
        require(annotation is product.BookFormData, f"{service_name} does not use BookFormData")

    require(product.policy.MAX_TITLE_LENGTH == 200, "title maximum is not 200")
    require(product.policy.MAX_AUTHOR_LENGTH == 100, "author maximum is not 100")
    require(product.policy.MAX_CATEGORY_LENGTH == 100, "category maximum is not 100")
    require(product.policy.MAX_ISBN_LENGTH == 20, "ISBN maximum is not 20")
    require(
        sorted(product.database.Base.metadata.tables) == ["books"],
        f"unexpected metadata tables: {sorted(product.database.Base.metadata.tables)!r}",
    )
    require("app.models.author" not in sys.modules, "Author Model was imported into the product flow")
    require("app.models.category" not in sys.modules, "Category Model was imported into the product flow")
    require(
        [column.key for column in product.Book.__table__.columns]
        == [
            "book_id",
            "title",
            "author",
            "category",
            "published_year",
            "price_cents",
            "stock_quantity",
            "isbn",
        ],
        "Book columns differ from the single-model contract",
    )
    require(not product.Book.__table__.foreign_keys, "Book still has Foreign Keys")
    require(not product.Book.__mapper__.relationships, "Book still has ORM relationships")
    require("author_id" not in product.Book.__table__.c, "Book.author_id still exists")
    require("category_id" not in product.Book.__table__.c, "Book.category_id still exists")
    require(isinstance(product.Book.__table__.c.author.type, String), "Book.author is not a String")
    require(isinstance(product.Book.__table__.c.category.type, String), "Book.category is not a String")
    require(product.Book.__table__.c.author.type.length == 100, "Book.author length differs")
    require(product.Book.__table__.c.category.type.length == 100, "Book.category length differs")
    require(product.Book.__table__.c.title.type.length == 200, "Book.title model length differs")
    require(product.Book.__table__.c.isbn.type.length == 20, "Book.isbn model length differs")
    require("price" not in product.Book.__table__.c, "legacy Book.price Column still exists")
    require(isinstance(product.Book.__table__.c.price_cents.type, BigInteger), "Book.price_cents is not BigInteger")
    require(product.policy.MAX_PRICE == Decimal("999999999999999.99"), "price maximum differs")
    require(product.policy.MAX_PRICE_INTEGER_DIGITS == 15, "price integer digit maximum differs")
    require(product.policy.MAX_PRICE_DECIMAL_PLACES == 2, "price decimal place maximum differs")
    require(product.policy.MAX_PRICE_CENTS == 99_999_999_999_999_999, "maximum cents differs")
    require(
        product.policy.MAX_PRICE_CENTS <= product.policy.SQLITE_SIGNED_64_MAX,
        "maximum cents exceeds SQLite signed 64-bit INTEGER",
    )
    require(
        event.contains(
            original_engine,
            "connect",
            product.database._enable_sqlite_foreign_keys,
        ),
        "application SQLite Engine listener is not registered",
    )


def check_url_for_source() -> None:
    template_paths = sorted((APPLICATION_DIRECTORY / "templates").glob("*.html"))
    template_source = "\n".join(path.read_text() for path in template_paths)
    route_source = (APPLICATION_DIRECTORY / "routers" / "book_router.py").read_text()

    for route_name in (
        "home",
        "book_list",
        "book_new",
        "book_create",
        "book_detail",
        "book_edit",
        "book_update",
        "book_delete",
    ):
        pattern = rf"request\.url_for\(\s*['\"]{route_name}['\"]"
        require(re.search(pattern, template_source) is not None, f"Template url_for missing: {route_name}")

    require(
        'request.url_for("book_detail"' in route_source,
        "Create/Update redirect does not use the book_detail Route Name",
    )
    require(
        'request.url_for("book_list"' in route_source,
        "Update/Delete redirect does not use the book_list Route Name",
    )
    require("/books/books" not in template_source + route_source, "double prefix literal found")
    require('action=""' not in template_source, "empty form action found")


def check_single_model_source() -> None:
    active_paths = [
        APPLICATION_DIRECTORY / "database.py",
        APPLICATION_DIRECTORY / "main.py",
        APPLICATION_DIRECTORY / "models" / "__init__.py",
        APPLICATION_DIRECTORY / "models" / "book.py",
        APPLICATION_DIRECTORY / "repositories" / "book_repository.py",
        APPLICATION_DIRECTORY / "routers" / "book_router.py",
        APPLICATION_DIRECTORY / "schemas" / "book.py",
        APPLICATION_DIRECTORY / "services" / "book_service.py",
        APPLICATION_DIRECTORY / "templates" / "book_form.html",
        APPLICATION_DIRECTORY / "templates" / "book_list.html",
        APPLICATION_DIRECTORY / "templates" / "book_detail.html",
    ]
    source = "\n".join(path.read_text() for path in active_paths)
    forbidden_tokens = (
        "author_id",
        "category_id",
        "ForeignKey(",
        "relationship(",
        "joinedload(",
        "author_repository",
        "category_repository",
        "seed_reference_data",
        "book.author.",
        "book.category.",
    )
    for token in forbidden_tokens:
        require(token not in source, f"active Book flow still contains {token!r}")


def check_price_cents_source() -> None:
    price_paths = [
        APPLICATION_DIRECTORY / "models" / "book.py",
        APPLICATION_DIRECTORY / "repositories" / "book_repository.py",
        APPLICATION_DIRECTORY / "routers" / "book_router.py",
        APPLICATION_DIRECTORY / "services" / "book_service.py",
        APPLICATION_DIRECTORY / "template_config.py",
        APPLICATION_DIRECTORY / "templates" / "book_list.html",
        APPLICATION_DIRECTORY / "templates" / "book_detail.html",
    ]
    source = "\n".join(path.read_text() for path in price_paths)
    for pattern, label in (
        (r"\bFloat\b", "Float"),
        (r"\bfloat\(", "float conversion"),
        (r"\bNumeric\(", "Numeric"),
        (r"Decimal\(\s*float", "Decimal(float)"),
        (r"book\.price(?!_cents)", "legacy book.price"),
    ):
        require(re.search(pattern, source) is None, f"active price flow still contains {label}")
    require(source.count("price_cents") >= 8, "price_cents is not connected across active layers")
    list_source = (APPLICATION_DIRECTORY / "templates" / "book_list.html").read_text()
    detail_source = (APPLICATION_DIRECTORY / "templates" / "book_detail.html").read_text()
    form_source = (APPLICATION_DIRECTORY / "templates" / "book_form.html").read_text()
    require("book.price_cents | format_price" in list_source, "Book list price Filter differs")
    require("book.price_cents | format_price" in detail_source, "Book detail price Filter differs")
    require("format_price" not in form_source, "Book Form uses the display price Filter")
    require("price_cents" not in form_source, "Book Form exposes cents directly")


def check_seed_cli_and_makefile(product: SimpleNamespace, temporary_root: Path) -> None:
    require(SEED_SQL_PATH.is_file(), "sql/seed.sql is missing")
    require(DB_CLI_PATH.is_file(), "scripts/db_cli.py is missing")
    require(MAKEFILE_PATH.is_file(), "Makefile is missing")

    application_source = "\n".join(
        path.read_text()
        for path in sorted(APPLICATION_DIRECTORY.rglob("*.py"))
    )
    require("INSERT INTO books" not in application_source, "Book seed SQL is hardcoded in product Python")
    require("seed_reference_data" not in application_source, "legacy Python seed function remains")

    seed_source = SEED_SQL_PATH.read_text()
    require("INSERT INTO books" in seed_source, "seed.sql does not insert Book data")
    require("INSERT OR IGNORE" not in seed_source.upper(), "seed.sql uses INSERT OR IGNORE")
    require("INSERT OR REPLACE" not in seed_source.upper(), "seed.sql uses INSERT OR REPLACE")
    require("authors" not in seed_source.lower(), "seed.sql references authors")
    require("categories" not in seed_source.lower(), "seed.sql references categories")
    require("price_cents" in seed_source, "seed.sql does not use price_cents")

    cli_help = subprocess.run(
        [sys.executable, str(DB_CLI_PATH), "--help"],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    require(cli_help.returncode == 0, f"db_cli.py --help failed: {cli_help.stderr}")
    require("seed" in cli_help.stdout and "reset" in cli_help.stdout, "CLI help omits seed/reset")
    for forbidden_command in ("db-init", "db-mock", "db-clear", "db-execute"):
        require(forbidden_command not in cli_help.stdout, f"CLI exposes {forbidden_command}")

    parser = product.db_cli.build_parser()
    command_action = next(action for action in parser._actions if action.dest == "command")
    require(set(command_action.choices) == {"seed", "reset"}, "CLI subcommands differ")

    original_seed_database = product.db_cli.seed_database
    original_reset_database = product.db_cli.reset_database
    try:
        product.db_cli.seed_database = lambda: 6
        seed_stdout = StringIO()
        with redirect_stdout(seed_stdout):
            seed_exit_code = product.db_cli.main(["seed"])
        require(seed_exit_code == 0, "successful seed CLI exit code differs")
        require("Database seeded successfully." in seed_stdout.getvalue(), "seed success message differs")
        require("Inserted 6 books." in seed_stdout.getvalue(), "seed inserted count is missing")

        product.db_cli.reset_database = lambda: 6
        reset_stdout = StringIO()
        with redirect_stdout(reset_stdout):
            reset_exit_code = product.db_cli.main(["reset"])
        require(reset_exit_code == 0, "successful reset CLI exit code differs")
        require("Database reset successfully." in reset_stdout.getvalue(), "reset success message differs")
        require("Inserted 6 books." in reset_stdout.getvalue(), "reset inserted count is missing")

        def fail_seed() -> int:
            raise product.db_cli.DatabaseCLIError("database already contains Book data")

        product.db_cli.seed_database = fail_seed
        seed_stderr = StringIO()
        with redirect_stderr(seed_stderr):
            failed_seed_exit_code = product.db_cli.main(["seed"])
        require(failed_seed_exit_code != 0, "failed seed CLI returned exit code 0")
        require("Seed failed:" in seed_stderr.getvalue(), "seed failure message differs")
    finally:
        product.db_cli.seed_database = original_seed_database
        product.db_cli.reset_database = original_reset_database

    makefile_source = MAKEFILE_PATH.read_text()
    make_targets = re.findall(r"^([A-Za-z0-9_-]+):", makefile_source, re.MULTILINE)
    expected_targets = ["help", "run", "test", "clean", "db-seed", "db-reset"]
    require(make_targets == expected_targets, f"Make targets differ: {make_targets!r}")
    phony_match = re.search(r"^\.PHONY:\s*(.+)$", makefile_source, re.MULTILINE)
    require(phony_match is not None, "Makefile .PHONY declaration is missing")
    require(set(phony_match.group(1).split()) == set(expected_targets), "Makefile .PHONY targets differ")
    require("$(PYTHON) -m uvicorn app.main:app --reload" in makefile_source, "make run command differs")
    require(
        "PYTHONDONTWRITEBYTECODE=1 $(PYTHON) scripts/regression_check.py" in makefile_source,
        "make test command differs",
    )
    require("$(PYTHON) scripts/db_cli.py seed" in makefile_source, "make db-seed command differs")
    require("$(PYTHON) scripts/db_cli.py reset" in makefile_source, "make db-reset command differs")
    for forbidden_target in ("db-init:", "db-mock:", "db-clear:", "db-execute:"):
        require(forbidden_target not in makefile_source, f"Makefile exposes {forbidden_target[:-1]}")

    make_help = subprocess.run(
        ["make", "-f", str(MAKEFILE_PATH), "help"],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    require(make_help.returncode == 0, f"make help failed: {make_help.stderr}")
    for target in expected_targets:
        require(f"make {target}" in make_help.stdout, f"make help omits {target}")

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
        require(table_names == ["books"], f"seed database tables differ: {table_names!r}")
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
    require(len(seed_isbns) == len(set(seed_isbns)), "seed ISBN values are duplicated")
    require(any(isbn.startswith("978") for isbn in seed_isbns), "seed has no 978 ISBN")
    require(any(isbn.startswith("979") for isbn in seed_isbns), "seed has no 979 ISBN")
    for seed_row, isbn in zip(seed_rows, seed_isbns):
        require(re.fullmatch(r"\d{13}", isbn) is not None, f"seed ISBN is not canonical: {isbn}")
        require(reference_isbn_13_checksum_is_valid(isbn), f"seed ISBN checksum is invalid: {isbn}")
        require(0 < len(seed_row[1]) <= product.policy.MAX_TITLE_LENGTH, "seed title length differs")
        require(0 < len(seed_row[2]) <= product.policy.MAX_AUTHOR_LENGTH, "seed author length differs")
        require(0 < len(seed_row[3]) <= product.policy.MAX_CATEGORY_LENGTH, "seed category length differs")
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
        [row[5] for row in seed_rows] == [0, 1050, 123456, 4999, 250000, 999999],
        "seed price_cents values differ",
    )
    require(any(row[6] == 0 for row in seed_rows), "seed has no zero-stock Book")

    rows_before_repeat = seed_rows
    try:
        product.db_cli.seed_database(
            project_root=cli_root,
            database_path=cli_database_path,
            seed_sql_path=cli_seed_path,
        )
    except product.db_cli.DatabaseCLIError as exc:
        require("already contains Book data" in str(exc), "repeat seed error message differs")
        require("make db-reset" in str(exc), "repeat seed does not recommend reset")
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
            "(book_id, title, author, category, published_year, price_cents, stock_quantity, isbn) "
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
    require(rows_after_repeat == rows_before_repeat, "repeat seed changed existing data")

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
            for row in connection.execute("SELECT book_id FROM books ORDER BY book_id")
        ]
    finally:
        connection.close()
    require(reset_ids == [1, 2, 3, 4, 5, 6], f"reset Book IDs differ: {reset_ids!r}")

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
        "(book_id, title, author, category, published_year, price_cents, stock_quantity, isbn) "
        "VALUES (1, 'Duplicate ID', 'Probe', 'Probe', 2025, 100, 1, '9786000000073');\n"
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
        rollback_count = connection.execute("SELECT COUNT(*) FROM books").fetchone()[0]
    finally:
        connection.close()
    require(rollback_count == 0, f"failed seed left {rollback_count} partial rows")

    clean_root = temporary_root / "make-clean-check"
    (clean_root / ".git").mkdir(parents=True)
    (clean_root / ".venv").mkdir()
    (clean_root / "nested" / "__pycache__").mkdir(parents=True)
    (clean_root / ".pytest_cache").mkdir()
    (clean_root / ".mypy_cache").mkdir()
    (clean_root / ".ruff_cache").mkdir()
    (clean_root / "__MACOSX").mkdir()
    (clean_root / ".git" / "protected.pyc").write_text("keep")
    (clean_root / ".venv" / "protected.pyc").write_text("keep")
    (clean_root / "nested" / "__pycache__" / "cache.pyc").write_text("remove")
    (clean_root / ".pytest_cache" / "state").write_text("remove")
    (clean_root / ".mypy_cache" / "state").write_text("remove")
    (clean_root / ".ruff_cache" / "state").write_text("remove")
    (clean_root / "__MACOSX" / "state").write_text("remove")
    (clean_root / ".DS_Store").write_text("remove")
    (clean_root / "._metadata").write_text("remove")

    make_clean = subprocess.run(
        ["make", "-f", str(MAKEFILE_PATH), "clean"],
        cwd=clean_root,
        check=False,
        capture_output=True,
        text=True,
    )
    require(make_clean.returncode == 0, f"make clean failed: {make_clean.stderr}")
    require((clean_root / ".git" / "protected.pyc").exists(), "make clean touched .git")
    require((clean_root / ".venv" / "protected.pyc").exists(), "make clean touched .venv")
    for removed_path in (
        clean_root / "nested" / "__pycache__",
        clean_root / ".pytest_cache",
        clean_root / ".mypy_cache",
        clean_root / ".ruff_cache",
        clean_root / "__MACOSX",
        clean_root / ".DS_Store",
        clean_root / "._metadata",
    ):
        require(not removed_path.exists(), f"make clean left {removed_path.name}")


def html_targets(body: str) -> HTMLTargets:
    targets = HTMLTargets()
    targets.feed(body)
    return targets


def target_paths(targets: list[str]) -> list[str]:
    return [urlsplit(target).path for target in targets]


def require_html(response: ASGIResponse, status_code: int, label: str) -> None:
    require(response.status_code == status_code, f"{label}: expected {status_code}, got {response.status_code}")
    content_type = response.headers.get("content-type", "")
    require(content_type.startswith("text/html"), f"{label}: response is not HTML")


def require_safe_rendered_urls(response: ASGIResponse, label: str) -> None:
    for invalid_text in ("/books/books", "{book_id}", 'action=""'):
        require(invalid_text not in response.body, f"{label}: found invalid rendered text {invalid_text}")


def require_paths(
    response: ASGIResponse,
    *,
    links: set[str] | None = None,
    actions: set[str] | None = None,
    label: str,
) -> None:
    targets = html_targets(response.body)
    rendered_links = set(target_paths(targets.links))
    rendered_actions = set(target_paths(targets.form_actions))
    for expected_link in links or set():
        require(expected_link in rendered_links, f"{label}: link missing {expected_link}")
    for expected_action in actions or set():
        require(expected_action in rendered_actions, f"{label}: form action missing {expected_action}")
    require("" not in targets.form_actions, f"{label}: empty form action rendered")
    require_safe_rendered_urls(response, label)


def field_fragment(body: str, field_name: str) -> str:
    field_position = body.find(f'name="{field_name}"')
    require(field_position >= 0, f"form field not rendered: {field_name}")
    next_field_position = body.find('<div class="field">', field_position)
    if next_field_position < 0:
        return body[field_position:]
    return body[field_position:next_field_position]


def require_field_error(
    response: ASGIResponse,
    field_name: str,
    expected_message: str,
    label: str,
) -> None:
    require_html(response, 200, label)
    fragment = field_fragment(response.body, field_name)
    require(expected_message in fragment, f"{label}: expected {field_name} error containing {expected_message!r}")


def require_original_input(response: ASGIResponse, field_name: str, value: str, label: str) -> None:
    fragment = field_fragment(response.body, field_name)
    expected_value = f'value="{escape(value, quote=True)}"'
    require(expected_value in fragment, f"{label}: original {field_name} value was not preserved")


def direct_request(product: SimpleNamespace, path: str) -> Any:
    requests = importlib.import_module("starlette.requests")
    return requests.Request(
        {
            "type": "http",
            "method": "POST",
            "path": path,
            "raw_path": path.encode(),
            "root_path": "",
            "scheme": "http",
            "query_string": b"",
            "headers": [(b"host", b"testserver")],
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 80),
            "app": product.app,
            "router": product.app.router,
        }
    )


def require_form_context_preserved(
    product: SimpleNamespace,
    *,
    book_id: int | None,
    form: dict[str, str],
) -> None:
    form_data = product.BookFormData(**form)
    require(form_data.to_form_data() == form, "BookFormData did not preserve the original strings")

    with product.database.SessionLocal() as db:
        if book_id is None:
            response = product.book_router_module.create_book(
                direct_request(product, "/books"),
                form_data,
                db,
            )
        else:
            response = product.book_router_module.update_book(
                direct_request(product, f"/books/{book_id}/edit"),
                book_id,
                form_data,
                db,
            )

    context = getattr(response, "context", None)
    require(context is not None, "TemplateResponse context is unavailable")
    require(context["form_data"] == form, "Form context did not preserve the original strings")


def require_stored_isbn(
    product: SimpleNamespace,
    book_id: int,
    expected_isbn: str,
    label: str,
) -> None:
    with product.database.SessionLocal() as db:
        book = db.get(product.Book, book_id)
        require(book is not None, f"{label}: stored Book is missing")
        require(book.isbn == expected_isbn, f"{label}: stored ISBN differs: {book.isbn!r}")
        require(
            re.fullmatch(r"\d{13}", book.isbn) is not None,
            f"{label}: stored ISBN is not 13 digits",
        )


def require_stored_book_text(
    product: SimpleNamespace,
    book_id: int,
    *,
    author: str,
    category: str,
    label: str,
) -> None:
    with product.database.SessionLocal() as db:
        book = db.get(product.Book, book_id)
        require(book is not None, f"{label}: stored Book is missing")
        require(book.author == author, f"{label}: stored author differs: {book.author!r}")
        require(book.category == category, f"{label}: stored category differs: {book.category!r}")


def require_stored_price_cents(
    product: SimpleNamespace,
    book_id: int,
    expected_cents: int,
    label: str,
) -> None:
    with product.database.SessionLocal() as db:
        book = db.get(product.Book, book_id)
        require(book is not None, f"{label}: stored Book is missing")
        require(book.price_cents == expected_cents, f"{label}: stored cents differ: {book.price_cents!r}")
        require(isinstance(book.price_cents, int), f"{label}: ORM cents is not int")


def generated_isbn_13(sequence: int) -> str:
    first_twelve_digits = f"978600{sequence:06d}"
    return first_twelve_digits + reference_isbn_13_check_digit(first_twelve_digits)


async def run_http_checks(product: SimpleNamespace) -> None:
    client = DirectASGIClient(product.app)
    state: dict[str, Any] = {}

    async def basic_pages() -> None:
        home = await client.request("GET", "/")
        require_html(home, 200, "GET /")
        require_paths(home, links={"/", "/books", "/books/new"}, label="home")

        book_list = await client.request("GET", "/books")
        require_html(book_list, 200, "GET /books")
        require_paths(
            book_list,
            links={"/", "/books", "/books/new"},
            actions={"/books"},
            label="book list",
        )

        empty_search = await client.request("GET", "/books", query=urlencode({"q": "missing"}))
        require_html(empty_search, 200, "GET /books?q=missing")
        require("No books found." in empty_search.body, "empty search result was not filtered")

        new_form = await client.request("GET", "/books/new")
        require_html(new_form, 200, "GET /books/new")
        require_paths(new_form, links={"/books"}, actions={"/books"}, label="create form")
        require_original_input(new_form, "stock_quantity", "1", "create stock default")
        require('maxlength="200"' in new_form.body, "title maxlength is not rendered as 200")
        require('name="author"' in new_form.body, "author text input is missing")
        require('name="category"' in new_form.body, "category text input is missing")
        require('maxlength="100"' in field_fragment(new_form.body, "author"), "author maxlength is not 100")
        require('maxlength="100"' in field_fragment(new_form.body, "category"), "category maxlength is not 100")
        require('name="author_id"' not in new_form.body, "legacy author_id field is rendered")
        require('name="category_id"' not in new_form.body, "legacy category_id field is rendered")
        require(
            'max="999999999999999.99"' in field_fragment(new_form.body, "price"),
            "price maximum is not rendered",
        )
        require('maxlength="20"' in new_form.body, "ISBN maxlength is not rendered as 20")
        require('pattern="[0-9Xx-]+"' in new_form.body, "ISBN input pattern does not allow X and x")

    await run_async_check("basic pages and template URLs", basic_pages)

    first_form = {
        "title": "  Regression   Alpha  ",
        "author": "  Jane Austen  ",
        "category": "  Fiction  ",
        "published_year": "2025",
        "price": "12.50",
        "stock_quantity": "3",
        "isbn": "0306406152",
    }
    second_form = {
        **first_form,
        "title": "Search Other",
        "isbn": "979-1-234-56789-6",
    }

    async def create_and_search_flow() -> None:
        first_create = await client.request("POST", "/books", form=first_form)
        require(first_create.status_code == 303, f"Create expected 303, got {first_create.status_code}")
        first_path = urlsplit(first_create.headers.get("location", "")).path
        require(re.fullmatch(r"/books/\d+", first_path) is not None, "Create redirect is not a detail URL")
        state["first_path"] = first_path
        state["first_id"] = int(first_path.rsplit("/", 1)[1])

        first_detail = await client.request("GET", first_path)
        require_html(first_detail, 200, "created Book detail")
        require("Regression Alpha" in first_detail.body, "normalized title was not stored")
        require("Regression   Alpha" not in first_detail.body, "title whitespace was not normalized")
        require("Jane Austen" in first_detail.body, "author text is missing from Book detail")
        require("Fiction" in first_detail.body, "category text is missing from Book detail")
        require("$12.50" in first_detail.body, "price cents were not rendered with currency format")
        require("9780306406157" in first_detail.body, "ISBN-10 was not rendered as canonical ISBN-13")
        require_stored_isbn(product, state["first_id"], "9780306406157", "numeric ISBN-10 Create")
        require_stored_price_cents(product, state["first_id"], 1250, "price Create")
        require_stored_book_text(
            product,
            state["first_id"],
            author="Jane Austen",
            category="Fiction",
            label="Book text Create",
        )
        require_paths(
            first_detail,
            links={"/books", f"{first_path}/edit"},
            actions={f"{first_path}/delete"},
            label="book detail",
        )

        second_create = await client.request("POST", "/books", form=second_form)
        require(second_create.status_code == 303, "second Create did not return 303")
        second_path = urlsplit(second_create.headers.get("location", "")).path
        state["second_path"] = second_path
        state["second_id"] = int(second_path.rsplit("/", 1)[1])
        require_stored_isbn(product, state["second_id"], "9791234567896", "hyphenated 979 ISBN-13 Create")

        search = await client.request("GET", "/books", query=urlencode({"q": "Alpha"}))
        require_html(search, 200, "GET /books?q=Alpha")
        require("Regression Alpha" in search.body, "matching search result is missing")
        require("Jane Austen" in search.body, "author text is missing from Book list")
        require("Fiction" in search.body, "category text is missing from Book list")
        require("Search Other" not in search.body, "non-matching search result was not filtered")
        require_paths(search, links={first_path}, actions={"/books"}, label="search result")

    await run_async_check("create and search flow", create_and_search_flow)

    async def update_flow() -> None:
        first_path = state["first_path"]
        first_id = state["first_id"]

        edit_form = await client.request("GET", f"{first_path}/edit")
        require_html(edit_form, 200, "GET Book edit")
        require_original_input(edit_form, "price", "12.50", "initial Update price")
        require_paths(
            edit_form,
            links={first_path},
            actions={f"{first_path}/edit"},
            label="update form",
        )

        same_isbn_form = {
            **first_form,
            "title": "Regression Updated",
            "author": "George Orwell",
            "category": "Dystopian Fiction",
            "price": "1234.56",
            "isbn": "0-306-40615-2",
        }
        update = await client.request("POST", f"{first_path}/edit", form=same_isbn_form)
        require(update.status_code == 303, f"Update expected 303, got {update.status_code}")
        require(urlsplit(update.headers.get("location", "")).path == first_path, "Update redirect differs")

        updated_detail = await client.request("GET", first_path)
        require_html(updated_detail, 200, "updated Book detail")
        require("Regression Updated" in updated_detail.body, "updated title is missing")
        require("George Orwell" in updated_detail.body, "updated author is missing")
        require("Dystopian Fiction" in updated_detail.body, "updated category is missing")
        require("$1,234.56" in updated_detail.body, "updated price cents were not rendered with currency format")
        require_stored_isbn(product, first_id, "9780306406157", "self ISBN expression Update")
        require_stored_price_cents(product, first_id, 123456, "price Update")
        require_stored_book_text(
            product,
            first_id,
            author="George Orwell",
            category="Dystopian Fiction",
            label="Book text Update",
        )

        duplicate_form = {**same_isbn_form, "isbn": second_form["isbn"]}
        duplicate = await client.request("POST", f"{first_path}/edit", form=duplicate_form)
        require_field_error(duplicate, "isbn", "ISBN already exists.", "duplicate ISBN Update")

        preserved_update = {
            **same_isbn_form,
            "title": "  Update   Original  ",
            "author": "   ",
            "category": "c" * 101,
            "price": "bad-update-price",
            "isbn": "bad update isbn",
        }
        invalid_update = await client.request("POST", f"{first_path}/edit", form=preserved_update)
        require_field_error(invalid_update, "author", "This field is required.", "Update author")
        require_field_error(invalid_update, "category", "Category must be at most 100 characters.", "Update category")
        require_original_input(invalid_update, "title", preserved_update["title"], "Update title")
        require_original_input(invalid_update, "author", preserved_update["author"], "Update author")
        require_original_input(invalid_update, "category", preserved_update["category"], "Update category")
        require_original_input(invalid_update, "price", preserved_update["price"], "Update price")
        require_original_input(invalid_update, "isbn", preserved_update["isbn"], "Update ISBN")
        require_form_context_preserved(
            product,
            book_id=first_id,
            form=preserved_update,
        )

        missing_update = await client.request("POST", "/books/999999/edit", form=same_isbn_form)
        require(missing_update.status_code == 303, "missing Book Update did not return 303")
        require(
            urlsplit(missing_update.headers.get("location", "")).path == "/books",
            "missing Book Update did not redirect to /books",
        )

    await run_async_check("update flow", update_flow)

    async def price_cents_create_update_flow() -> None:
        price_cases = [
            ("0", 0),
            ("00", 0),
            ("0.00", 0),
            ("10", 1000),
            ("10.0", 1000),
            ("10.00", 1000),
            ("10.5", 1050),
            ("00010.50", 1050),
            ("1234.56", 123456),
            ("999999999999999", 99_999_999_999_999_900),
            ("999999999999999.99", 99_999_999_999_999_999),
        ]
        for sequence, (raw_price, expected_cents) in enumerate(price_cases, start=1):
            isbn = generated_isbn_13(sequence)
            price_form = {
                **first_form,
                "title": f"Price Flow {sequence}",
                "price": raw_price,
                "isbn": isbn,
            }
            created = await client.request("POST", "/books", form=price_form)
            require(created.status_code == 303, f"price Create {raw_price!r} did not return 303")
            created_path = urlsplit(created.headers.get("location", "")).path
            created_id = int(created_path.rsplit("/", 1)[1])
            require_stored_price_cents(
                product,
                created_id,
                expected_cents,
                f"price Create {raw_price!r}",
            )

            detail = await client.request("GET", created_path)
            require_html(detail, 200, f"price Detail {raw_price!r}")
            expected_display = f"${Decimal(expected_cents) / Decimal('100'):,.2f}"
            require(
                expected_display in detail.body,
                f"price Detail {raw_price!r} missing {expected_display!r}",
            )

            edit_form = await client.request("GET", f"{created_path}/edit")
            require_html(edit_form, 200, f"price Edit Form {raw_price!r}")
            expected_form_value = format(Decimal(expected_cents) / Decimal("100"), ".2f")
            require_original_input(
                edit_form,
                "price",
                expected_form_value,
                f"restored price Form {raw_price!r}",
            )
            price_fragment = field_fragment(edit_form.body, "price")
            require("$" not in price_fragment, f"price Edit Form {raw_price!r} contains currency symbol")
            require("," not in price_fragment, f"price Edit Form {raw_price!r} contains comma")

            updated = await client.request(
                "POST",
                f"{created_path}/edit",
                form={**price_form, "title": f"Price Updated {sequence}"},
            )
            require(updated.status_code == 303, f"price Update {raw_price!r} did not return 303")
            require(
                urlsplit(updated.headers.get("location", "")).path == created_path,
                f"price Update {raw_price!r} redirect differs",
            )
            require_stored_price_cents(
                product,
                created_id,
                expected_cents,
                f"price Update {raw_price!r}",
            )

        price_list = await client.request("GET", "/books")
        require_html(price_list, 200, "price display Book list")
        for expected_display in (
            "$0.00",
            "$10.50",
            "$1,234.56",
            "$999,999,999,999,999.99",
        ):
            require(expected_display in price_list.body, f"Book list missing price display {expected_display!r}")
        require("$1,050.00" not in price_list.body, "1050 cents was displayed as $1,050.00")
        require(">1050<" not in price_list.body, "raw 1050 cents was displayed in Book list")
        require(">1,050.00<" not in price_list.body, "Book list displayed price without currency symbol")

    await run_async_check("price cents Create and Update flow", price_cents_create_update_flow)

    async def isbn_canonicalization_and_duplicates() -> None:
        first_path = state["first_path"]

        equivalent_first_isbns = (
            "03-064-0615-2",
            "9780306406157",
            "978-03-0640615-7",
        )
        for isbn in equivalent_first_isbns:
            duplicate = await client.request(
                "POST",
                "/books",
                form={**first_form, "title": f"Duplicate {isbn}", "isbn": isbn},
            )
            require_field_error(
                duplicate,
                "isbn",
                "ISBN already exists.",
                f"equivalent ISBN duplicate {isbn}",
            )

        lowercase_x_form = {
            **first_form,
            "title": "Lowercase X ISBN",
            "isbn": "0-8044-2957-x",
        }
        lowercase_x_create = await client.request("POST", "/books", form=lowercase_x_form)
        require(lowercase_x_create.status_code == 303, "lowercase x ISBN-10 Create did not return 303")
        lowercase_x_path = urlsplit(lowercase_x_create.headers.get("location", "")).path
        lowercase_x_id = int(lowercase_x_path.rsplit("/", 1)[1])
        require_stored_isbn(product, lowercase_x_id, "9780804429573", "lowercase x ISBN-10 Create")

        for isbn in ("080442957X", "080442957x", "978-0-8044-2957-3"):
            duplicate = await client.request(
                "POST",
                "/books",
                form={**lowercase_x_form, "title": f"Duplicate {isbn}", "isbn": isbn},
            )
            require_field_error(
                duplicate,
                "isbn",
                "ISBN already exists.",
                f"X/canonical ISBN duplicate {isbn}",
            )

        self_expression_update = await client.request(
            "POST",
            f"{lowercase_x_path}/edit",
            form={**lowercase_x_form, "title": "Uppercase X Update", "isbn": "080442957X"},
        )
        require(self_expression_update.status_code == 303, "self ISBN expression Update did not return 303")
        require(
            urlsplit(self_expression_update.headers.get("location", "")).path == lowercase_x_path,
            "self ISBN expression Update redirect differs",
        )
        require_stored_isbn(product, lowercase_x_id, "9780804429573", "uppercase X self Update")

        other_book_duplicate = await client.request(
            "POST",
            f"{first_path}/edit",
            form={**first_form, "title": "Other ISBN Duplicate", "isbn": "9780804429573"},
        )
        require_field_error(
            other_book_duplicate,
            "isbn",
            "ISBN already exists.",
            "other Book canonical ISBN Update",
        )

    await run_async_check(
        "ISBN canonicalization and duplicate handling",
        isbn_canonicalization_and_duplicates,
    )

    async def validation() -> None:
        valid_form = {
            "title": "Validation Base",
            "author": "Validation Author",
            "category": "Validation Category",
            "published_year": "2025",
            "price": "10.00",
            "stock_quantity": "1",
            "isbn": "9781234567897",
        }
        long_isbn = "1-2-3-4-5-6-7-8-90123"
        require(len(long_isbn) == 21, "long ISBN test input is not 21 characters")
        require(len(long_isbn.replace("-", "")) == 13, "long ISBN test input is not 13 digits")
        with product.database.SessionLocal() as db:
            book_count_before = db.query(product.Book).count()

        cases = [
            ("blank title", {"title": ""}, "title", "This field is required."),
            ("long title", {"title": "x" * 201}, "title", "Title must be at most 200 characters."),
            ("blank Author", {"author": ""}, "author", "This field is required."),
            ("blank Category", {"category": ""}, "category", "This field is required."),
            ("whitespace Author", {"author": "   "}, "author", "This field is required."),
            ("whitespace Category", {"category": "   "}, "category", "This field is required."),
            (
                "long Author",
                {"author": "a" * 101},
                "author",
                "Author must be at most 100 characters.",
            ),
            (
                "long Category",
                {"category": "c" * 101},
                "category",
                "Category must be at most 100 characters.",
            ),
            ("invalid year", {"published_year": "year"}, "published_year", "This field must be an integer."),
            (
                "year below range",
                {"published_year": str(product.policy.MIN_PUBLISHED_YEAR - 1)},
                "published_year",
                "Published year must be between",
            ),
            (
                "year above range",
                {"published_year": str(product.policy.MAX_FUTURE_PUBLICATION_YEAR + 1)},
                "published_year",
                "Published year must be between",
            ),
            ("blank price", {"price": ""}, "price", "This field is required."),
            ("whitespace price", {"price": "   "}, "price", "This field is required."),
            ("negative zero price", {"price": "-0"}, "price", "Price must be 0 or greater."),
            ("negative zero decimal price", {"price": "-0.00"}, "price", "Price must be 0 or greater."),
            ("negative integer price", {"price": "-1"}, "price", "Price must be 0 or greater."),
            ("negative decimal price", {"price": "-0.01"}, "price", "Price must be 0 or greater."),
            ("plus price", {"price": "+10"}, "price", "Price must be a decimal amount"),
            ("alphabetic price", {"price": "abc"}, "price", "Price must be a decimal amount"),
            ("mixed price", {"price": "10a"}, "price", "Price must be a decimal amount"),
            ("currency-symbol price", {"price": "$10"}, "price", "Price must be a decimal amount"),
            ("comma price", {"price": "1,000"}, "price", "Price must be a decimal amount"),
            ("trailing decimal point price", {"price": "10."}, "price", "Price must be a decimal amount"),
            ("leading decimal point price", {"price": ".50"}, "price", "Price must be a decimal amount"),
            ("three zero decimal price", {"price": "10.000"}, "price", "Price may have at most 2 decimal places."),
            ("three decimal price", {"price": "10.123"}, "price", "Price may have at most 2 decimal places."),
            ("lowercase exponent price", {"price": "1e3"}, "price", "Price must be a decimal amount"),
            ("uppercase exponent price", {"price": "1E3"}, "price", "Price must be a decimal amount"),
            ("NaN price", {"price": "NaN"}, "price", "Price must be a finite number."),
            ("Infinity price", {"price": "Infinity"}, "price", "Price must be a finite number."),
            ("-Infinity price", {"price": "-Infinity"}, "price", "Price must be a finite number."),
            (
                "integer price above maximum",
                {"price": "1000000000000000"},
                "price",
                "Price must be at most",
            ),
            (
                "decimal price above maximum",
                {"price": "1000000000000000.00"},
                "price",
                "Price must be at most",
            ),
            (
                "maximum price with excess scale",
                {"price": "999999999999999.999"},
                "price",
                "Price may have at most 2 decimal places.",
            ),
            ("invalid stock", {"stock_quantity": "stock"}, "stock_quantity", "This field must be an integer."),
            ("negative stock", {"stock_quantity": "-1"}, "stock_quantity", "Stock quantity must be between"),
            (
                "stock above maximum",
                {"stock_quantity": str(product.policy.MAX_STOCK_QUANTITY + 1)},
                "stock_quantity",
                "Stock quantity must be between",
            ),
            ("blank ISBN", {"isbn": ""}, "isbn", "This field is required."),
            (
                "ISBN-10 character in first nine",
                {"isbn": "03064X6152"},
                "isbn",
                "ISBN-10 must use digits in the first 9 positions",
            ),
            (
                "ISBN-10 invalid final character",
                {"isbn": "030640615A"},
                "isbn",
                "ISBN contains characters that are not allowed.",
            ),
            (
                "ISBN-10 invalid check digit",
                {"isbn": "0306406153"},
                "isbn",
                "ISBN-10 check digit is invalid.",
            ),
            (
                "ISBN-13 character",
                {"isbn": "97803064061X7"},
                "isbn",
                "ISBN-13 must contain digits only.",
            ),
            (
                "ISBN-13 invalid prefix",
                {"isbn": "9770306406158"},
                "isbn",
                "ISBN-13 must start with 978 or 979.",
            ),
            (
                "ISBN-13 invalid check digit",
                {"isbn": "9780306406158"},
                "isbn",
                "ISBN-13 check digit is invalid.",
            ),
            ("ISBN internal space", {"isbn": "978030 6406157"}, "isbn", "ISBN must not contain whitespace."),
            ("ISBN start hyphen", {"isbn": "-0306406152"}, "isbn", "ISBN must not start with a hyphen."),
            ("ISBN end hyphen", {"isbn": "0306406152-"}, "isbn", "ISBN must not end with a hyphen."),
            (
                "ISBN double hyphen",
                {"isbn": "0--306406152"},
                "isbn",
                "ISBN must not contain consecutive hyphens.",
            ),
            ("ISBN character count", {"isbn": "03064061520"}, "isbn", "10 or 13 characters"),
            ("ISBN over 20 characters", {"isbn": long_isbn}, "isbn", "ISBN must be at most 20 characters."),
            ("duplicate ISBN", {"isbn": second_form["isbn"]}, "isbn", "ISBN already exists."),
        ]

        for label, overrides, field_name, expected_message in cases:
            response = await client.request("POST", "/books", form={**valid_form, **overrides})
            require_field_error(response, field_name, expected_message, label)
            if field_name == "price":
                require_original_input(response, "price", overrides["price"], label)

        missing_stock_form = dict(valid_form)
        missing_stock_form.pop("stock_quantity")
        missing_stock = await client.request("POST", "/books", form=missing_stock_form)
        require_field_error(missing_stock, "stock_quantity", "This field is required.", "missing stock field")

        preserved_create = {
            **valid_form,
            "title": "  Create   Original  ",
            "author": "   ",
            "category": "c" * 101,
            "price": "00010.50",
            "isbn": "bad create isbn",
        }
        preserved_response = await client.request("POST", "/books", form=preserved_create)
        require_field_error(
            preserved_response,
            "author",
            "This field is required.",
            "preserved Create author",
        )
        require_field_error(
            preserved_response,
            "category",
            "Category must be at most 100 characters.",
            "preserved Create category",
        )
        require_original_input(preserved_response, "title", preserved_create["title"], "Create title")
        require_original_input(preserved_response, "author", preserved_create["author"], "Create author")
        require_original_input(preserved_response, "category", preserved_create["category"], "Create category")
        require_original_input(preserved_response, "price", preserved_create["price"], "Create price")
        require_original_input(preserved_response, "isbn", preserved_create["isbn"], "Create ISBN")
        require_form_context_preserved(product, book_id=None, form=preserved_create)

        with product.database.SessionLocal() as db:
            require(
                db.query(product.Book).count() == book_count_before,
                "invalid author/category Form stored a Book",
            )

    await run_async_check("validation and original input preservation", validation)

    async def errors_and_delete_flow() -> None:
        first_path = state["first_path"]

        get_delete = await client.request("GET", f"{first_path}/delete")
        require_html(get_delete, 405, "GET delete")
        require("Method not allowed." in get_delete.body, "GET delete error message differs")
        require_paths(get_delete, links={"/", "/books"}, label="405 error")

        missing_detail = await client.request("GET", "/books/999999")
        require_html(missing_detail, 404, "missing Book detail")
        require("Book not found." in missing_detail.body, "missing Book detail message differs")
        require_paths(missing_detail, links={"/", "/books"}, label="Book 404 error")

        missing_edit = await client.request("GET", "/books/999999/edit")
        require_html(missing_edit, 404, "missing Book edit")
        require("Book not found." in missing_edit.body, "missing Book edit message differs")

        invalid_id = await client.request("GET", "/books/not-an-integer")
        require_html(invalid_id, 422, "invalid book_id")
        require("Invalid request." in invalid_id.body, "invalid book_id message differs")
        require_paths(invalid_id, links={"/", "/books"}, label="422 error")

        missing_url = await client.request("GET", "/not-a-real-page")
        require_html(missing_url, 404, "missing general URL")
        require("Page not found." in missing_url.body, "general 404 message differs")
        require_paths(missing_url, links={"/", "/books"}, label="general 404 error")

        deleted = await client.request("POST", f"{first_path}/delete", form={})
        require(deleted.status_code == 303, "Delete did not return 303")
        require(urlsplit(deleted.headers.get("location", "")).path == "/books", "Delete redirect differs")

        missing_delete = await client.request("POST", "/books/999999/delete", form={})
        require(missing_delete.status_code == 303, "missing Book Delete did not return 303")
        require(
            urlsplit(missing_delete.headers.get("location", "")).path == "/books",
            "missing Book Delete did not redirect to /books",
        )

    await run_async_check("error pages and delete flow", errors_and_delete_flow)


def check_database_behavior(product: SimpleNamespace, temporary_engine: Any) -> None:
    with temporary_engine.connect() as connection:
        pragma_value = connection.scalar(text("PRAGMA foreign_keys"))
        table_names = list(
            connection.scalars(
                text(
                    "SELECT name FROM sqlite_master "
                    "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' "
                    "ORDER BY name"
                )
            ).all()
        )
        table_info = connection.execute(text("PRAGMA table_info(books)")).all()
        foreign_keys = connection.execute(text("PRAGMA foreign_key_list(books)")).all()
        indexes = connection.execute(text("PRAGMA index_list(books)")).all()

    require(pragma_value == 1, f"PRAGMA foreign_keys expected 1, got {pragma_value}")
    require(table_names == ["books"], f"unexpected user tables: {table_names!r}")
    column_types = {row[1]: row[2] for row in table_info}
    require(
        list(column_types)
        == [
            "book_id",
            "title",
            "author",
            "category",
            "published_year",
            "price_cents",
            "stock_quantity",
            "isbn",
        ],
        f"books columns differ: {list(column_types)!r}",
    )
    require(column_types["author"] == "VARCHAR(100)", "books.author DB type differs")
    require(column_types["category"] == "VARCHAR(100)", "books.category DB type differs")
    require("price" not in column_types, "legacy books.price Column still exists")
    require("INT" in column_types["price_cents"], "books.price_cents is not INTEGER-compatible")
    require(not foreign_keys, f"books still has Foreign Keys: {foreign_keys!r}")
    require(any(row[2] == 1 for row in indexes), "books ISBN Unique index is missing")

    with product.database.SessionLocal() as db:
        valid_book = product.Book(
            title="Direct Single Model",
            author="Direct Author",
            category="Direct Category",
            published_year=2025,
            price_cents=1000,
            stock_quantity=1,
            isbn="9780000000019",
        )
        db.add(valid_book)
        db.commit()
        require(valid_book.book_id is not None, "single-model direct insert failed")
        require(valid_book.author == "Direct Author", "direct author string was not stored")
        require(valid_book.category == "Direct Category", "direct category string was not stored")
        require(valid_book.price_cents == 1000, "direct cents value was not stored")
        require(isinstance(valid_book.price_cents, int), "Book.price_cents ORM value is not int")

        db.add(
            product.Book(
                title="Duplicate Direct ISBN",
                author="Other Author",
                category="Other Category",
                published_year=2025,
                price_cents=1000,
                stock_quantity=1,
                isbn=valid_book.isbn,
            )
        )
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
        else:
            raise CheckFailure("ISBN Unique constraint did not reject a duplicate")

        maximum_price_book = product.Book(
            title="Maximum Cents",
            author="Maximum Author",
            category="Maximum Category",
            published_year=2025,
            price_cents=product.policy.MAX_PRICE_CENTS,
            stock_quantity=1,
            isbn="9780000000040",
        )
        db.add(maximum_price_book)
        db.commit()
        db.refresh(maximum_price_book)
        require(maximum_price_book.book_id is not None, "valid insert after rollback failed")
        require(
            maximum_price_book.price_cents == 99_999_999_999_999_999,
            "maximum cents did not round-trip exactly",
        )
        require(isinstance(maximum_price_book.price_cents, int), "maximum ORM cents is not int")
        raw_cents, storage_type = db.execute(
            text("SELECT price_cents, typeof(price_cents) FROM books WHERE book_id = :book_id"),
            {"book_id": maximum_price_book.book_id},
        ).one()
        require(raw_cents == 99_999_999_999_999_999, "raw SQLite maximum cents differs")
        require(storage_type == "integer", f"raw SQLite cents storage type differs: {storage_type!r}")


def main() -> int:
    initial_working_directory = Path.cwd()
    library_before = snapshot_file(LIBRARY_DATABASE_PATH)
    temporary_root_path: Path | None = None
    regression_failed = False

    try:
        run_check("Python compile", check_python_compile)

        with TemporaryDirectory(prefix="b5-2-regression-") as temporary_directory:
            temporary_root_path = Path(temporary_directory)
            product = run_check(
                "application import",
                lambda: import_application(temporary_root_path),
            )

            original_engine = product.database.engine
            original_session_local = product.database.SessionLocal
            temporary_database_path = temporary_root_path / "regression.db"
            temporary_engine = create_engine(
                f"sqlite:///{temporary_database_path}",
                connect_args={"check_same_thread": False},
            )
            event.listen(
                temporary_engine,
                "connect",
                product.database._enable_sqlite_foreign_keys,
            )
            product.database.engine = temporary_engine
            product.database.SessionLocal = sessionmaker(
                autocommit=False,
                autoflush=False,
                bind=temporary_engine,
            )

            try:
                os.chdir(PROJECT_ROOT)
                run_check(
                    "isolated database setup",
                    product.database.init_db,
                )
                run_check(
                    "route and single-model contracts",
                    lambda: check_route_and_phase_contracts(product, original_engine),
                )
                run_check("ISBN policy and conversion", lambda: check_isbn_policy(product))
                run_check("price policy and cents conversion", lambda: check_price_policy(product))
                run_check("price display formatting", lambda: check_price_display(product))
                run_check("url_for source structure", check_url_for_source)
                run_check("single-model source structure", check_single_model_source)
                run_check("price cents source structure", check_price_cents_source)
                run_check(
                    "seed CLI and Makefile",
                    lambda: check_seed_cli_and_makefile(product, temporary_root_path),
                )
                asyncio.run(run_http_checks(product))
                run_check(
                    "sqlite single table and constraints",
                    lambda: check_database_behavior(product, temporary_engine),
                )
            finally:
                product.database.engine = original_engine
                product.database.SessionLocal = original_session_local
                temporary_engine.dispose()
    except StopRegressionChecks:
        regression_failed = True
    except Exception as exc:
        detail = str(exc) or type(exc).__name__
        print(f"[FAIL] regression setup: {detail}", file=sys.stderr)
        regression_failed = True
    finally:
        os.chdir(initial_working_directory)

    library_after = snapshot_file(LIBRARY_DATABASE_PATH)
    if library_after == library_before:
        print("[PASS] library.db unchanged")
    else:
        print(
            f"[FAIL] library.db unchanged: before={library_before!r}, after={library_after!r}",
            file=sys.stderr,
        )
        regression_failed = True

    if temporary_root_path is None or not temporary_root_path.exists():
        print("[PASS] temporary artifacts cleaned")
    else:
        print(f"[FAIL] temporary artifacts cleaned: {temporary_root_path} remains", file=sys.stderr)
        regression_failed = True

    if regression_failed:
        print("Regression checks failed.", file=sys.stderr)
        return 1

    print("All regression checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
