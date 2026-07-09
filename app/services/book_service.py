import math
from typing import Any

from sqlalchemy.orm import Session

from app.models.author import Author
from app.models.book import Book
from app.models.category import Category
from app.repositories import author_repository, book_repository, category_repository


class BookValidationError(Exception):
    def __init__(self, errors: dict[str, str]) -> None:
        self.errors = errors
        super().__init__("Book validation failed")


def list_books(db: Session, q: str | None = None) -> list[Book]:
    search_text = q.strip() if q else None
    return book_repository.get_books(db, search_text)


def get_book_detail(db: Session, book_id: int) -> Book | None:
    return book_repository.get_book(db, book_id)


def get_book_form_options(db: Session) -> dict[str, list[Author] | list[Category]]:
    return {
        "authors": author_repository.get_authors(db),
        "categories": category_repository.get_categories(db),
    }


def create_book(db: Session, data: dict[str, Any]) -> Book:
    book_data = _validate_book_data(db, data)
    return book_repository.create_book(db, **book_data)


def update_book(db: Session, book_id: int, data: dict[str, Any]) -> Book | None:
    book = book_repository.get_book(db, book_id)
    if book is None:
        return None

    book_data = _validate_book_data(db, data, current_book_id=book_id)
    return book_repository.update_book(db, book, **book_data)


def delete_book(db: Session, book_id: int) -> bool:
    book = book_repository.get_book(db, book_id)
    if book is None:
        return False

    book_repository.delete_book(db, book)
    return True


def _validate_book_data(
    db: Session,
    data: dict[str, Any],
    current_book_id: int | None = None,
) -> dict[str, Any]:
    errors: dict[str, str] = {}

    title = _clean_required_text(data, "title", errors)
    isbn = _clean_required_text(data, "isbn", errors)
    author_id = _parse_required_int(data, "author_id", errors)
    category_id = _parse_required_int(data, "category_id", errors)
    published_year = _parse_required_int(data, "published_year", errors)
    price = _parse_required_float(data, "price", errors)
    stock_quantity = _parse_required_int(data, "stock_quantity", errors)

    if published_year is not None and published_year <= 0:
        errors["published_year"] = "Published year must be a positive integer."

    if price is not None:
        if not math.isfinite(price):
            errors["price"] = "This field must be numeric."
        elif price < 0:
            errors["price"] = "Price must be 0 or greater."

    if stock_quantity is not None and stock_quantity < 0:
        errors["stock_quantity"] = "Stock quantity must be 0 or greater."

    if author_id is not None and author_repository.get_author(db, author_id) is None:
        errors["author_id"] = "Selected author does not exist."

    if category_id is not None and category_repository.get_category(db, category_id) is None:
        errors["category_id"] = "Selected category does not exist."

    if isbn:
        existing_book = book_repository.get_book_by_isbn(db, isbn)
        if existing_book and existing_book.book_id != current_book_id:
            errors["isbn"] = "ISBN already exists."

    if errors:
        raise BookValidationError(errors)

    return {
        "title": title,
        "author_id": author_id,
        "category_id": category_id,
        "published_year": published_year,
        "price": price,
        "stock_quantity": stock_quantity,
        "isbn": isbn,
    }


def _clean_required_text(
    data: dict[str, Any],
    field_name: str,
    errors: dict[str, str],
) -> str | None:
    value = data.get(field_name)
    if value is None:
        errors[field_name] = "This field is required."
        return None

    text = str(value).strip()
    if not text:
        errors[field_name] = "This field is required."
        return None

    return text


def _parse_required_int(
    data: dict[str, Any],
    field_name: str,
    errors: dict[str, str],
) -> int | None:
    value = data.get(field_name)
    if value is None or str(value).strip() == "":
        errors[field_name] = "This field is required."
        return None

    try:
        return int(str(value).strip())
    except ValueError:
        errors[field_name] = "This field must be an integer."
        return None


def _parse_required_float(
    data: dict[str, Any],
    field_name: str,
    errors: dict[str, str],
) -> float | None:
    value = data.get(field_name)
    if value is None or str(value).strip() == "":
        errors[field_name] = "This field is required."
        return None

    try:
        return float(str(value).strip())
    except ValueError:
        errors[field_name] = "This field must be numeric."
        return None
