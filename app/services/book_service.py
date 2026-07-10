from datetime import date
from decimal import Decimal, InvalidOperation
import re
from typing import Any

from sqlalchemy.orm import Session

from app.models.author import Author
from app.models.book import Book
from app.models.category import Category
from app.repositories import author_repository, book_repository, category_repository

CURRENT_YEAR = date.today().year
MIN_PUBLISHED_YEAR = 1000
MAX_FUTURE_PUBLICATION_YEAR_OFFSET = 1
MAX_FUTURE_PUBLICATION_YEAR = CURRENT_YEAR + MAX_FUTURE_PUBLICATION_YEAR_OFFSET
MIN_PRICE = Decimal("0")
MAX_PRICE = Decimal("10000000")
MAX_PRICE_DECIMAL_PLACES = 2
MIN_STOCK_QUANTITY = 0
MAX_STOCK_QUANTITY = 100_000
ISBN_10_LENGTH = 10
ISBN_13_LENGTH = 13
VALID_ISBN_DIGIT_LENGTHS = {ISBN_10_LENGTH, ISBN_13_LENGTH}
ISBN_ALLOWED_PATTERN = re.compile(r"^[0-9-]+$")


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

    title = _clean_title(data, errors)
    isbn = _clean_isbn(data, errors)
    author_id = _parse_required_int(data, "author_id", errors)
    category_id = _parse_required_int(data, "category_id", errors)
    published_year = _parse_required_int(data, "published_year", errors)
    price = _parse_required_price(data, errors)
    stock_quantity = _parse_required_int(data, "stock_quantity", errors)

    if published_year is not None and not (MIN_PUBLISHED_YEAR <= published_year <= MAX_FUTURE_PUBLICATION_YEAR):
        errors["published_year"] = (
            f"Published year must be between {MIN_PUBLISHED_YEAR} and {MAX_FUTURE_PUBLICATION_YEAR}."
        )

    if stock_quantity is not None and not (MIN_STOCK_QUANTITY <= stock_quantity <= MAX_STOCK_QUANTITY):
        errors["stock_quantity"] = f"Stock quantity must be between {MIN_STOCK_QUANTITY} and {MAX_STOCK_QUANTITY}."

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
        "price": float(price),
        "stock_quantity": stock_quantity,
        "isbn": isbn,
    }


def _clean_title(
    data: dict[str, Any],
    errors: dict[str, str],
) -> str | None:
    value = data.get("title")
    if value is None:
        errors["title"] = "This field is required."
        return None

    text = " ".join(str(value).strip().split())
    if not text:
        errors["title"] = "This field is required."
        return None

    return text


def _clean_isbn(data: dict[str, Any], errors: dict[str, str]) -> str | None:
    value = data.get("isbn")
    if value is None:
        errors["isbn"] = "This field is required."
        return None

    isbn = str(value).strip()
    if not isbn:
        errors["isbn"] = "This field is required."
        return None

    if (
        any(character.isspace() for character in isbn)
        or not ISBN_ALLOWED_PATTERN.fullmatch(isbn)
        or isbn.startswith("-")
        or isbn.endswith("-")
        or "--" in isbn
        or len(isbn.replace("-", "")) not in VALID_ISBN_DIGIT_LENGTHS
    ):
        errors["isbn"] = "ISBN must contain 10 or 13 digits and may use single hyphens between digit groups."
        return None

    return isbn


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


def _parse_required_price(
    data: dict[str, Any],
    errors: dict[str, str],
) -> Decimal | None:
    value = data.get("price")
    if value is None or str(value).strip() == "":
        errors["price"] = "This field is required."
        return None

    try:
        price = Decimal(str(value).strip())
    except InvalidOperation:
        errors["price"] = "This field must be numeric."
        return None

    if not price.is_finite():
        errors["price"] = "This field must be numeric."
        return None

    if price < MIN_PRICE or price > MAX_PRICE:
        errors["price"] = f"Price must be between {MIN_PRICE} and {MAX_PRICE}."
        return None

    if price.as_tuple().exponent < -MAX_PRICE_DECIMAL_PLACES:
        errors["price"] = f"Price may have at most {MAX_PRICE_DECIMAL_PLACES} decimal places."
        return None

    return price
