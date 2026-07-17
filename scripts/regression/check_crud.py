from __future__ import annotations

import re
from urllib.parse import urlencode, urlsplit

from scripts.regression.assertions import (
    require,
    require_field_error,
    require_html,
    require_original_input,
    require_paths,
)
from scripts.regression.context import RegressionContext
from scripts.regression.fixtures import (
    create_primary_and_secondary,
    primary_book_form,
    require_form_context_preserved,
    require_stored_book_text,
    require_stored_isbn,
    require_stored_price_cents,
    secondary_book_form,
)


async def check_create_and_search(context: RegressionContext) -> None:
    context.reset_database()
    product = context.product
    client = context.client
    require(product is not None, "application modules are unavailable")
    require(client is not None, "ASGI client is unavailable")
    first_form = primary_book_form()
    second_form = secondary_book_form()

    first_create = await client.request("POST", "/books", form=first_form)
    require(
        first_create.status_code == 303,
        f"Create expected 303, got {first_create.status_code}",
    )
    first_path = urlsplit(first_create.headers.get("location", "")).path
    require(
        re.fullmatch(r"/books/\d+", first_path) is not None,
        "Create redirect is not a detail URL",
    )
    first_id = int(first_path.rsplit("/", 1)[1])

    first_detail = await client.request("GET", first_path)
    require_html(first_detail, 200, "created Book detail")
    require("Regression Alpha" in first_detail.body, "normalized title was not stored")
    require(
        "Regression   Alpha" not in first_detail.body,
        "title whitespace was not normalized",
    )
    require("Jane Austen" in first_detail.body, "author text is missing from Book detail")
    require("Fiction" in first_detail.body, "category text is missing from Book detail")
    require(
        "$12.50" in first_detail.body,
        "price cents were not rendered with currency format",
    )
    require(
        "9780306406157" in first_detail.body,
        "ISBN-10 was not rendered as canonical ISBN-13",
    )
    require_stored_isbn(product, first_id, "9780306406157", "numeric ISBN-10 Create")
    require_stored_price_cents(product, first_id, 1250, "price Create")
    require_stored_book_text(
        product,
        first_id,
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
    second_id = int(second_path.rsplit("/", 1)[1])
    require_stored_isbn(
        product,
        second_id,
        "9791234567896",
        "hyphenated 979 ISBN-13 Create",
    )

    search = await client.request(
        "GET",
        "/books",
        query=urlencode({"q": "Alpha"}),
    )
    require_html(search, 200, "GET /books?q=Alpha")
    require("Regression Alpha" in search.body, "matching search result is missing")
    require("Jane Austen" in search.body, "author text is missing from Book list")
    require("Fiction" in search.body, "category text is missing from Book list")
    require(
        "Search Other" not in search.body,
        "non-matching search result was not filtered",
    )
    require_paths(search, links={first_path}, actions={"/books"}, label="search result")


async def check_update(context: RegressionContext) -> None:
    context.reset_database()
    product = context.product
    client = context.client
    require(product is not None, "application modules are unavailable")
    require(client is not None, "ASGI client is unavailable")
    first, _second = await create_primary_and_secondary(client)
    first_form = primary_book_form()
    second_form = secondary_book_form()

    edit_form = await client.request("GET", f"{first.path}/edit")
    require_html(edit_form, 200, "GET Book edit")
    require_original_input(edit_form, "price", "12.50", "initial Update price")
    require_paths(
        edit_form,
        links={first.path},
        actions={f"{first.path}/edit"},
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
    update = await client.request(
        "POST",
        f"{first.path}/edit",
        form=same_isbn_form,
    )
    require(update.status_code == 303, f"Update expected 303, got {update.status_code}")
    require(
        urlsplit(update.headers.get("location", "")).path == first.path,
        "Update redirect differs",
    )

    updated_detail = await client.request("GET", first.path)
    require_html(updated_detail, 200, "updated Book detail")
    require("Regression Updated" in updated_detail.body, "updated title is missing")
    require("George Orwell" in updated_detail.body, "updated author is missing")
    require(
        "Dystopian Fiction" in updated_detail.body,
        "updated category is missing",
    )
    require(
        "$1,234.56" in updated_detail.body,
        "updated price cents were not rendered with currency format",
    )
    require_stored_isbn(
        product,
        first.book_id,
        "9780306406157",
        "self ISBN expression Update",
    )
    require_stored_price_cents(product, first.book_id, 123456, "price Update")
    require_stored_book_text(
        product,
        first.book_id,
        author="George Orwell",
        category="Dystopian Fiction",
        label="Book text Update",
    )

    duplicate_form = {**same_isbn_form, "isbn": second_form["isbn"]}
    duplicate = await client.request(
        "POST",
        f"{first.path}/edit",
        form=duplicate_form,
    )
    require_field_error(
        duplicate,
        "isbn",
        "ISBN already exists.",
        "duplicate ISBN Update",
    )

    preserved_update = {
        **same_isbn_form,
        "title": "  Update   Original  ",
        "author": "   ",
        "category": "c" * 101,
        "price": "bad-update-price",
        "isbn": "bad update isbn",
    }
    invalid_update = await client.request(
        "POST",
        f"{first.path}/edit",
        form=preserved_update,
    )
    require_field_error(
        invalid_update,
        "author",
        "This field is required.",
        "Update author",
    )
    require_field_error(
        invalid_update,
        "category",
        "Category must be at most 100 characters.",
        "Update category",
    )
    for field_name in ("title", "author", "category", "price", "isbn"):
        require_original_input(
            invalid_update,
            field_name,
            preserved_update[field_name],
            f"Update {field_name}",
        )
    require_form_context_preserved(
        product,
        book_id=first.book_id,
        form=preserved_update,
    )

    missing_update = await client.request(
        "POST",
        "/books/999999/edit",
        form=same_isbn_form,
    )
    require(missing_update.status_code == 303, "missing Book Update did not return 303")
    require(
        urlsplit(missing_update.headers.get("location", "")).path == "/books",
        "missing Book Update did not redirect to /books",
    )
