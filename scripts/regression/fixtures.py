from __future__ import annotations

from dataclasses import dataclass
import importlib
import re
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlsplit

from scripts.regression.assertions import require
from scripts.regression.client import ASGIResponse, DirectASGIClient


@dataclass(frozen=True)
class CreatedBook:
    path: str
    book_id: int
    response: ASGIResponse


def primary_book_form() -> dict[str, str]:
    return {
        "title": "  Regression   Alpha  ",
        "author": "  Jane Austen  ",
        "category": "  Fiction  ",
        "published_year": "2025",
        "price": "12.50",
        "stock_quantity": "3",
        "isbn": "0306406152",
    }


def secondary_book_form() -> dict[str, str]:
    return {
        **primary_book_form(),
        "title": "Search Other",
        "isbn": "979-1-234-56789-6",
    }


async def create_book(
    client: DirectASGIClient,
    form: dict[str, str],
    *,
    label: str,
) -> CreatedBook:
    response = await client.request("POST", "/books", form=form)
    require(response.status_code == 303, f"{label}: expected 303, got {response.status_code}")
    path = urlsplit(response.headers.get("location", "")).path
    require(re.fullmatch(r"/books/\d+", path) is not None, f"{label}: redirect is not a detail URL")
    return CreatedBook(path=path, book_id=int(path.rsplit("/", 1)[1]), response=response)


async def create_primary_and_secondary(
    client: DirectASGIClient,
) -> tuple[CreatedBook, CreatedBook]:
    primary = await create_book(client, primary_book_form(), label="primary Create")
    secondary = await create_book(client, secondary_book_form(), label="secondary Create")
    return primary, secondary


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


def generated_isbn_13(sequence: int) -> str:
    first_twelve_digits = f"978600{sequence:06d}"
    return first_twelve_digits + reference_isbn_13_check_digit(first_twelve_digits)


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
    require(
        form_data.to_form_data() == form,
        "BookFormData did not preserve the original strings",
    )

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
    require(
        context["form_data"] == form,
        "Form context did not preserve the original strings",
    )


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
        require(
            book.category == category,
            f"{label}: stored category differs: {book.category!r}",
        )


def require_stored_price_cents(
    product: SimpleNamespace,
    book_id: int,
    expected_cents: int,
    label: str,
) -> None:
    with product.database.SessionLocal() as db:
        book = db.get(product.Book, book_id)
        require(book is not None, f"{label}: stored Book is missing")
        require(
            book.price_cents == expected_cents,
            f"{label}: stored cents differ: {book.price_cents!r}",
        )
        require(isinstance(book.price_cents, int), f"{label}: ORM cents is not int")
