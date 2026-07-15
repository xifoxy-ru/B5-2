from __future__ import annotations

import asyncio
from dataclasses import dataclass, fields
from html import escape
from html.parser import HTMLParser
import importlib
import inspect
import os
from pathlib import Path
import re
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import Any, Callable, Coroutine
from urllib.parse import urlencode, urlsplit

from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker


PROJECT_ROOT = Path(__file__).resolve().parents[1]
APPLICATION_DIRECTORY = PROJECT_ROOT / "app"
LIBRARY_DATABASE_PATH = PROJECT_ROOT / "library.db"

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
        author_model = importlib.import_module("app.models.author")
        book_model = importlib.import_module("app.models.book")
        category_model = importlib.import_module("app.models.category")
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
        Author=author_model.Author,
        Book=book_model.Book,
        Category=category_model.Category,
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
            "author_id",
            "category_id",
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
    require(product.policy.MAX_ISBN_LENGTH == 20, "ISBN maximum is not 20")
    require(product.Book.__table__.c.title.type.length == 200, "Book.title model length differs")
    require(product.Book.__table__.c.isbn.type.length == 20, "Book.isbn model length differs")
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
        require('maxlength="20"' in new_form.body, "ISBN maxlength is not rendered as 20")
        require('pattern="[0-9Xx-]+"' in new_form.body, "ISBN input pattern does not allow X and x")

    await run_async_check("basic pages and template URLs", basic_pages)

    first_form = {
        "title": "  Regression   Alpha  ",
        "author_id": "1",
        "category_id": "1",
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
        require("9780306406157" in first_detail.body, "ISBN-10 was not rendered as canonical ISBN-13")
        require_stored_isbn(product, state["first_id"], "9780306406157", "numeric ISBN-10 Create")
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
        require("Search Other" not in search.body, "non-matching search result was not filtered")
        require_paths(search, links={first_path}, actions={"/books"}, label="search result")

    await run_async_check("create and search flow", create_and_search_flow)

    async def update_flow() -> None:
        first_path = state["first_path"]
        first_id = state["first_id"]

        edit_form = await client.request("GET", f"{first_path}/edit")
        require_html(edit_form, 200, "GET Book edit")
        require_paths(
            edit_form,
            links={first_path},
            actions={f"{first_path}/edit"},
            label="update form",
        )

        same_isbn_form = {
            **first_form,
            "title": "Regression Updated",
            "isbn": "0-306-40615-2",
        }
        update = await client.request("POST", f"{first_path}/edit", form=same_isbn_form)
        require(update.status_code == 303, f"Update expected 303, got {update.status_code}")
        require(urlsplit(update.headers.get("location", "")).path == first_path, "Update redirect differs")

        updated_detail = await client.request("GET", first_path)
        require_html(updated_detail, 200, "updated Book detail")
        require("Regression Updated" in updated_detail.body, "updated title is missing")
        require_stored_isbn(product, first_id, "9780306406157", "self ISBN expression Update")

        duplicate_form = {**same_isbn_form, "isbn": second_form["isbn"]}
        duplicate = await client.request("POST", f"{first_path}/edit", form=duplicate_form)
        require_field_error(duplicate, "isbn", "ISBN already exists.", "duplicate ISBN Update")

        preserved_update = {
            **same_isbn_form,
            "title": "  Update   Original  ",
            "author_id": "999997",
            "category_id": "999998",
            "price": "bad-update-price",
            "isbn": "bad update isbn",
        }
        invalid_update = await client.request("POST", f"{first_path}/edit", form=preserved_update)
        require_field_error(invalid_update, "author_id", "Selected author does not exist.", "Update author")
        require_field_error(invalid_update, "category_id", "Selected category does not exist.", "Update category")
        require_original_input(invalid_update, "title", preserved_update["title"], "Update title")
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
            "author_id": "1",
            "category_id": "1",
            "published_year": "2025",
            "price": "10.00",
            "stock_quantity": "1",
            "isbn": "9781234567897",
        }
        long_isbn = "1-2-3-4-5-6-7-8-90123"
        require(len(long_isbn) == 21, "long ISBN test input is not 21 characters")
        require(len(long_isbn.replace("-", "")) == 13, "long ISBN test input is not 13 digits")

        cases = [
            ("blank title", {"title": ""}, "title", "This field is required."),
            ("long title", {"title": "x" * 201}, "title", "Title must be at most 200 characters."),
            ("blank Author", {"author_id": ""}, "author_id", "This field is required."),
            ("blank Category", {"category_id": ""}, "category_id", "This field is required."),
            ("missing Author", {"author_id": "999999"}, "author_id", "Selected author does not exist."),
            ("missing Category", {"category_id": "999999"}, "category_id", "Selected category does not exist."),
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
            ("invalid price", {"price": "price"}, "price", "This field must be numeric."),
            ("negative price", {"price": "-0.01"}, "price", "Price must be between"),
            (
                "price above maximum",
                {"price": str(product.policy.MAX_PRICE + 1)},
                "price",
                "Price must be between",
            ),
            ("three decimal price", {"price": "1.001"}, "price", "Price may have at most 2 decimal places."),
            ("NaN price", {"price": "NaN"}, "price", "This field must be numeric."),
            ("Infinity price", {"price": "Infinity"}, "price", "This field must be numeric."),
            ("-Infinity price", {"price": "-Infinity"}, "price", "This field must be numeric."),
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

        missing_stock_form = dict(valid_form)
        missing_stock_form.pop("stock_quantity")
        missing_stock = await client.request("POST", "/books", form=missing_stock_form)
        require_field_error(missing_stock, "stock_quantity", "This field is required.", "missing stock field")

        preserved_create = {
            **valid_form,
            "title": "  Create   Original  ",
            "author_id": "999997",
            "category_id": "999998",
            "price": "bad-create-price",
            "isbn": "bad create isbn",
        }
        preserved_response = await client.request("POST", "/books", form=preserved_create)
        require_field_error(
            preserved_response,
            "author_id",
            "Selected author does not exist.",
            "preserved Create author",
        )
        require_field_error(
            preserved_response,
            "category_id",
            "Selected category does not exist.",
            "preserved Create category",
        )
        require_original_input(preserved_response, "title", preserved_create["title"], "Create title")
        require_original_input(preserved_response, "price", preserved_create["price"], "Create price")
        require_original_input(preserved_response, "isbn", preserved_create["isbn"], "Create ISBN")
        require_form_context_preserved(product, book_id=None, form=preserved_create)

        require(
            f'value="{preserved_create["author_id"]}"' not in preserved_response.body,
            "nonexistent Author unexpectedly rendered as an option",
        )
        require(
            f'value="{preserved_create["category_id"]}"' not in preserved_response.body,
            "nonexistent Category unexpectedly rendered as an option",
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
    require(pragma_value == 1, f"PRAGMA foreign_keys expected 1, got {pragma_value}")

    with product.database.SessionLocal() as db:
        valid_book = product.Book(
            title="Direct Valid FK",
            author_id=1,
            category_id=1,
            published_year=2025,
            price=10.0,
            stock_quantity=1,
            isbn="9780000000019",
        )
        db.add(valid_book)
        db.commit()
        require(valid_book.book_id is not None, "valid direct FK insert failed")

        db.add(
            product.Book(
                title="Invalid Author FK",
                author_id=999999,
                category_id=1,
                published_year=2025,
                price=10.0,
                stock_quantity=1,
                isbn="9780000000026",
            )
        )
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
        else:
            raise CheckFailure("invalid Author FK was accepted")

        db.add(
            product.Book(
                title="Invalid Category FK",
                author_id=1,
                category_id=999999,
                published_year=2025,
                price=10.0,
                stock_quantity=1,
                isbn="9780000000033",
            )
        )
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
        else:
            raise CheckFailure("invalid Category FK was accepted")

        valid_after_rollback = product.Book(
            title="Valid After Rollback",
            author_id=1,
            category_id=1,
            published_year=2025,
            price=10.0,
            stock_quantity=1,
            isbn="9780000000040",
        )
        db.add(valid_after_rollback)
        db.commit()
        require(valid_after_rollback.book_id is not None, "valid insert after rollback failed")

        db.add(
            product.Book(
                title="Duplicate Direct ISBN",
                author_id=1,
                category_id=1,
                published_year=2025,
                price=10.0,
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

        invalid_references = product.BookFormData(
            title="Service Invalid References",
            author_id="999997",
            category_id="999998",
            published_year="2025",
            price="10.00",
            stock_quantity="1",
            isbn="9780000000057",
        )
        try:
            product.book_service.create_book(db, invalid_references)
        except product.book_service.BookValidationError as exc:
            require("author_id" in exc.errors, "Service Author existence validation is missing")
            require("category_id" in exc.errors, "Service Category existence validation is missing")
        else:
            raise CheckFailure("Service accepted nonexistent Author/Category references")


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
                    lambda: (product.database.init_db(), product.database.seed_reference_data()),
                )
                run_check(
                    "route and Phase 1-5 contracts",
                    lambda: check_route_and_phase_contracts(product, original_engine),
                )
                run_check("ISBN policy and conversion", lambda: check_isbn_policy(product))
                run_check("url_for source structure", check_url_for_source)
                asyncio.run(run_http_checks(product))
                run_check(
                    "sqlite foreign keys and constraints",
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
