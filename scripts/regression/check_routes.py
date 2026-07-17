from __future__ import annotations

from dataclasses import fields
import inspect
from urllib.parse import urlencode, urlsplit

from scripts.regression.assertions import (
    field_fragment,
    require,
    require_html,
    require_original_input,
    require_paths,
)
from scripts.regression.context import RegressionContext
from scripts.regression.fixtures import create_book, primary_book_form


EXPECTED_ROUTES = [
    ("home", "/", frozenset({"GET"})),
    ("book_list", "/books", frozenset({"GET"})),
    ("book_new", "/books/new", frozenset({"GET"})),
    ("book_create", "/books", frozenset({"POST"})),
    ("book_detail", "/books/{book_id}", frozenset({"GET"})),
    ("book_edit", "/books/{book_id}/edit", frozenset({"GET"})),
    ("book_update", "/books/{book_id}/edit", frozenset({"POST"})),
    ("book_delete", "/books/{book_id}/delete", frozenset({"POST"})),
]


def check_contracts(context: RegressionContext) -> None:
    product = context.product
    require(product is not None, "application modules are unavailable")
    home_router = product.home_router_module.router
    book_router = product.book_router_module.router
    central_router = product.central_router_module.router
    actual_routes = [
        (route.name, route.path, frozenset(route.methods or set()))
        for route in [*home_router.routes, *book_router.routes]
    ]

    require(
        actual_routes == EXPECTED_ROUTES,
        f"route order/contract differs: {actual_routes!r}",
    )
    route_names = [route_name for route_name, _, _ in actual_routes]
    require(
        len(route_names) == len(set(route_names)),
        "business route names are duplicated",
    )
    require(
        all("/books/books" not in path for _, path, _ in actual_routes),
        "double /books prefix found",
    )
    require(book_router.prefix == "/books", "Book Router prefix is not /books")

    included_routers = [
        getattr(route, "original_router", None)
        for route in central_router.routes
    ]
    require(
        included_routers == [home_router, book_router],
        "central Router assembly/order differs",
    )
    app_includes = [
        getattr(route, "original_router", None)
        for route in product.app.routes
    ]
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
        route_path = next(
            path
            for name, path, _ in EXPECTED_ROUTES
            if name == route_name
        )
        path_parameters = {"book_id": "7"} if "{book_id}" in route_path else {}
        actual_url = str(
            product.app.url_path_for(route_name, **path_parameters)
        )
        require(
            actual_url == expected_url,
            f"{route_name} generated {actual_url}, expected {expected_url}",
        )

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
        inspect.signature(
            product.book_router_module._book_form_data
        ).return_annotation
        is product.BookFormData,
        "Router form dependency does not return BookFormData",
    )
    for service_name in ("create_book", "update_book"):
        annotation = inspect.signature(
            getattr(product.book_service, service_name)
        ).parameters["form_data"].annotation
        require(
            annotation is product.BookFormData,
            f"{service_name} does not use BookFormData",
        )


async def check_basic_pages(context: RegressionContext) -> None:
    context.reset_database()
    client = context.client
    require(client is not None, "ASGI client is unavailable")

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

    empty_search = await client.request(
        "GET",
        "/books",
        query=urlencode({"q": "missing"}),
    )
    require_html(empty_search, 200, "GET /books?q=missing")
    require(
        "No books found." in empty_search.body,
        "empty search result was not filtered",
    )

    new_form = await client.request("GET", "/books/new")
    require_html(new_form, 200, "GET /books/new")
    require_paths(new_form, links={"/books"}, actions={"/books"}, label="create form")
    require_original_input(new_form, "stock_quantity", "1", "create stock default")
    require('maxlength="200"' in new_form.body, "title maxlength is not rendered as 200")
    require('name="author"' in new_form.body, "author text input is missing")
    require('name="category"' in new_form.body, "category text input is missing")
    require(
        'maxlength="100"' in field_fragment(new_form.body, "author"),
        "author maxlength is not 100",
    )
    require(
        'maxlength="100"' in field_fragment(new_form.body, "category"),
        "category maxlength is not 100",
    )
    require('name="author_id"' not in new_form.body, "legacy author_id field is rendered")
    require(
        'name="category_id"' not in new_form.body,
        "legacy category_id field is rendered",
    )
    require(
        'max="999999999999999.99"' in field_fragment(new_form.body, "price"),
        "price maximum is not rendered",
    )
    require('maxlength="20"' in new_form.body, "ISBN maxlength is not rendered as 20")
    require(
        'pattern="[0-9Xx-]+"' in new_form.body,
        "ISBN input pattern does not allow X and x",
    )


async def check_error_pages_and_delete(context: RegressionContext) -> None:
    context.reset_database()
    client = context.client
    require(client is not None, "ASGI client is unavailable")
    created = await create_book(
        client,
        primary_book_form(),
        label="error-flow Book Create",
    )

    get_delete = await client.request("GET", f"{created.path}/delete")
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

    deleted = await client.request("POST", f"{created.path}/delete", form={})
    require(deleted.status_code == 303, "Delete did not return 303")
    require(
        urlsplit(deleted.headers.get("location", "")).path == "/books",
        "Delete redirect differs",
    )

    missing_delete = await client.request("POST", "/books/999999/delete", form={})
    require(missing_delete.status_code == 303, "missing Book Delete did not return 303")
    require(
        urlsplit(missing_delete.headers.get("location", "")).path == "/books",
        "missing Book Delete did not redirect to /books",
    )
