from datetime import date
from decimal import Decimal
import re

MAX_TITLE_LENGTH = 200

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
MAX_ISBN_LENGTH = 20
VALID_ISBN_LENGTHS = {ISBN_10_LENGTH, ISBN_13_LENGTH}
ISBN_13_PREFIXES = ("978", "979")
ISBN_ALLOWED_PATTERN = re.compile(r"^[0-9X]+$")


class ISBNValidationError(ValueError):
    pass


def canonicalize_isbn(value: str) -> str:
    isbn = str(value).strip()

    if isbn.startswith("-"):
        raise ISBNValidationError("ISBN must not start with a hyphen.")

    if isbn.endswith("-"):
        raise ISBNValidationError("ISBN must not end with a hyphen.")

    if "--" in isbn:
        raise ISBNValidationError("ISBN must not contain consecutive hyphens.")

    if any(character.isspace() for character in isbn):
        raise ISBNValidationError("ISBN must not contain whitespace.")

    if len(isbn) > MAX_ISBN_LENGTH:
        raise ISBNValidationError(f"ISBN must be at most {MAX_ISBN_LENGTH} characters.")

    compact_isbn = isbn.replace("-", "").replace("x", "X")

    if len(compact_isbn) not in VALID_ISBN_LENGTHS:
        raise ISBNValidationError("ISBN must contain 10 or 13 characters after removing hyphens.")

    if not ISBN_ALLOWED_PATTERN.fullmatch(compact_isbn):
        raise ISBNValidationError("ISBN contains characters that are not allowed.")

    if len(compact_isbn) == ISBN_10_LENGTH:
        if not _has_valid_isbn_10_characters(compact_isbn):
            raise ISBNValidationError(
                "ISBN-10 must use digits in the first 9 positions and a digit or X as the check digit."
            )
        if not _has_valid_isbn_10_checksum(compact_isbn):
            raise ISBNValidationError("ISBN-10 check digit is invalid.")
        return _convert_isbn_10_to_isbn_13(compact_isbn)

    if not _has_valid_isbn_13_characters(compact_isbn):
        raise ISBNValidationError("ISBN-13 must contain digits only.")
    if not _has_valid_isbn_13_prefix(compact_isbn):
        raise ISBNValidationError("ISBN-13 must start with 978 or 979.")
    if not _has_valid_isbn_13_checksum(compact_isbn):
        raise ISBNValidationError("ISBN-13 check digit is invalid.")
    return compact_isbn


def _has_valid_isbn_10_characters(isbn: str) -> bool:
    return isbn[:9].isdigit() and (isbn[9].isdigit() or isbn[9] == "X")


def _has_valid_isbn_10_checksum(isbn: str) -> bool:
    digits = [int(character) for character in isbn[:9]]
    check_digit = 10 if isbn[9] == "X" else int(isbn[9])
    weighted_sum = sum(
        weight * digit
        for weight, digit in zip(range(10, 1, -1), digits)
    )
    return (weighted_sum + check_digit) % 11 == 0


def _has_valid_isbn_13_characters(isbn: str) -> bool:
    return isbn.isdigit()


def _has_valid_isbn_13_prefix(isbn: str) -> bool:
    return isbn.startswith(ISBN_13_PREFIXES)


def _has_valid_isbn_13_checksum(isbn: str) -> bool:
    return isbn[-1] == _calculate_isbn_13_check_digit(isbn[:12])


def _calculate_isbn_13_check_digit(first_twelve_digits: str) -> str:
    weighted_sum = sum(
        int(character) * (1 if index % 2 == 0 else 3)
        for index, character in enumerate(first_twelve_digits)
    )
    return str((10 - weighted_sum % 10) % 10)


def _convert_isbn_10_to_isbn_13(isbn: str) -> str:
    first_twelve_digits = f"978{isbn[:9]}"
    return first_twelve_digits + _calculate_isbn_13_check_digit(first_twelve_digits)
