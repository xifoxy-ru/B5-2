from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy.orm import Session

from app.models.book import Book
from app.repositories import book_repository
from app.schemas.book import BookFormData
from app.services.book_validation_policy import (
    ISBNValidationError,
    MAX_AUTHOR_LENGTH,
    MAX_CATEGORY_LENGTH,
    MAX_FUTURE_PUBLICATION_YEAR,
    MAX_PRICE,
    MAX_PRICE_CENTS,
    MAX_PRICE_DECIMAL_PLACES,
    MAX_PRICE_INTEGER_DIGITS,
    MAX_STOCK_QUANTITY,
    MAX_TITLE_LENGTH,
    MIN_PRICE,
    MIN_PUBLISHED_YEAR,
    MIN_STOCK_QUANTITY,
    NON_FINITE_PRICE_INPUTS,
    PRICE_CENTS_PER_UNIT,
    PRICE_DECIMAL_CANDIDATE_PATTERN,
    PRICE_INPUT_PATTERN,
    SQLITE_SIGNED_64_MAX,
    canonicalize_isbn,
)


class BookValidationError(Exception):
    def __init__(self, errors: dict[str, str]) -> None:
        self.errors = errors
        super().__init__("Book validation failed")


def list_books(db: Session, q: str | None = None) -> list[Book]:
    search_text = q.strip() if q else None
    return book_repository.get_books(db, search_text)


def get_book_detail(db: Session, book_id: int) -> Book | None:
    return book_repository.get_book(db, book_id)


def create_book(db: Session, form_data: BookFormData) -> Book:
    book_data = _validate_book_data(db, form_data)
    return book_repository.create_book(db, **book_data)


def update_book(db: Session, book_id: int, form_data: BookFormData) -> Book | None:
    book = book_repository.get_book(db, book_id)
    if book is None:
        return None

    book_data = _validate_book_data(db, form_data, current_book_id=book_id)
    return book_repository.update_book(db, book, **book_data)


def delete_book(db: Session, book_id: int) -> bool:
    book = book_repository.get_book(db, book_id)
    if book is None:
        return False

    book_repository.delete_book(db, book)
    return True


def _validate_book_data(
    db: Session,
    form_data: BookFormData,
    current_book_id: int | None = None,
) -> dict[str, Any]:
    errors: dict[str, str] = {}

    title = _clean_title(form_data.title, errors)
    isbn = _clean_isbn(form_data.isbn, errors)
    author = _clean_required_text(
        form_data.author,
        field_name="author",
        field_label="Author",
        max_length=MAX_AUTHOR_LENGTH,
        errors=errors,
    )
    category = _clean_required_text(
        form_data.category,
        field_name="category",
        field_label="Category",
        max_length=MAX_CATEGORY_LENGTH,
        errors=errors,
    )
    published_year = _parse_required_int(form_data.published_year, "published_year", errors)
    price_cents = _parse_required_price_cents(form_data.price, errors)
    stock_quantity = _parse_required_int(form_data.stock_quantity, "stock_quantity", errors)

    if published_year is not None and not (MIN_PUBLISHED_YEAR <= published_year <= MAX_FUTURE_PUBLICATION_YEAR):
        errors["published_year"] = (
            f"Published year must be between {MIN_PUBLISHED_YEAR} and {MAX_FUTURE_PUBLICATION_YEAR}."
        )

    if stock_quantity is not None and not (MIN_STOCK_QUANTITY <= stock_quantity <= MAX_STOCK_QUANTITY):
        errors["stock_quantity"] = f"Stock quantity must be between {MIN_STOCK_QUANTITY} and {MAX_STOCK_QUANTITY}."

    if isbn:
        existing_book = book_repository.get_book_by_isbn(db, isbn)
        if existing_book and existing_book.book_id != current_book_id:
            errors["isbn"] = "ISBN already exists."

    if errors:
        raise BookValidationError(errors)

    return {
        "title": title,
        "author": author,
        "category": category,
        "published_year": published_year,
        "price_cents": price_cents,
        "stock_quantity": stock_quantity,
        "isbn": isbn,
    }


def _clean_title(
    value: str,
    errors: dict[str, str],
) -> str | None:
    if value is None:
        errors["title"] = "This field is required."
        return None

    text = " ".join(str(value).strip().split())
    if not text:
        errors["title"] = "This field is required."
        return None

    if len(text) > MAX_TITLE_LENGTH:
        errors["title"] = f"Title must be at most {MAX_TITLE_LENGTH} characters."
        return None

    return text


def _clean_required_text(
    value: str,
    field_name: str,
    field_label: str,
    max_length: int,
    errors: dict[str, str],
) -> str | None:
    if value is None:
        errors[field_name] = "This field is required."
        return None

    text = str(value).strip()
    if not text:
        errors[field_name] = "This field is required."
        return None

    if len(text) > max_length:
        errors[field_name] = f"{field_label} must be at most {max_length} characters."
        return None

    return text


def _clean_isbn(value: str, errors: dict[str, str]) -> str | None:
    if value is None:
        errors["isbn"] = "This field is required."
        return None

    if not str(value).strip():
        errors["isbn"] = "This field is required."
        return None

    try:
        return canonicalize_isbn(value)
    except ISBNValidationError as exc:
        errors["isbn"] = str(exc)
        return None


def _parse_required_int(
    value: str,
    field_name: str,
    errors: dict[str, str],
) -> int | None:
    if value is None or str(value).strip() == "":
        errors[field_name] = "This field is required."
        return None

    try:
        return int(str(value).strip())
    except ValueError:
        errors[field_name] = "This field must be an integer."
        return None


def _parse_required_price_cents(
    value: str,
    errors: dict[str, str],
) -> int | None:
    if value is None or str(value).strip() == "":
        errors["price"] = "This field is required."
        return None

    text = str(value).strip()
    has_allowed_format = PRICE_INPUT_PATTERN.fullmatch(text) is not None
    has_policy_candidate_format = (
        PRICE_DECIMAL_CANDIDATE_PATTERN.fullmatch(text) is not None
        or text in NON_FINITE_PRICE_INPUTS
    )
    if not has_allowed_format and not has_policy_candidate_format:
        errors["price"] = "Price must be a decimal amount with up to 2 decimal places."
        return None

    try:
        price = Decimal(text)
    except InvalidOperation:
        errors["price"] = "Price must be a decimal amount with up to 2 decimal places."
        return None

    if not price.is_finite():
        errors["price"] = "Price must be a finite number."
        return None

    if text.startswith("-") or price < MIN_PRICE:
        errors["price"] = "Price must be 0 or greater."
        return None

    fractional_part = text.partition(".")[2]
    if len(fractional_part) > MAX_PRICE_DECIMAL_PLACES:
        errors["price"] = f"Price may have at most {MAX_PRICE_DECIMAL_PLACES} decimal places."
        return None

    integer_part = text.partition(".")[0]
    significant_integer_part = integer_part.lstrip("0") or "0"
    if len(significant_integer_part) > MAX_PRICE_INTEGER_DIGITS or price > MAX_PRICE:
        errors["price"] = f"Price must be at most {MAX_PRICE}."
        return None

    price_cents_decimal = price * PRICE_CENTS_PER_UNIT
    if price_cents_decimal != price_cents_decimal.to_integral_value():
        errors["price"] = f"Price may have at most {MAX_PRICE_DECIMAL_PLACES} decimal places."
        return None

    price_cents = int(price_cents_decimal)
    if price_cents > MAX_PRICE_CENTS or price_cents > SQLITE_SIGNED_64_MAX:
        errors["price"] = f"Price must be at most {MAX_PRICE}."
        return None

    return price_cents


def price_cents_to_decimal(price_cents: int) -> Decimal:
    return Decimal(price_cents) / PRICE_CENTS_PER_UNIT


def price_cents_to_form_value(price_cents: int) -> str:
    return format(price_cents_to_decimal(price_cents), ".2f")
