from __future__ import annotations

import re
from urllib.parse import urlsplit

from scripts.regression.assertions import CheckFailure, require, require_field_error
from scripts.regression.context import RegressionContext
from scripts.regression.fixtures import (
    create_primary_and_secondary,
    primary_book_form,
    reference_isbn_10_checksum_is_valid,
    reference_isbn_13_check_digit,
    reference_isbn_13_checksum_is_valid,
    require_stored_isbn,
    secondary_book_form,
)


def check_policy(context: RegressionContext) -> None:
    product = context.product
    require(product is not None, "application modules are unavailable")
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
        require(
            canonical_isbn == expected_isbn,
            f"{label}: canonical ISBN differs",
        )
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
        (
            "ISBN-13 invalid prefix",
            "9770306406158",
            "ISBN-13 must start with 978 or 979",
        ),
        (
            "ISBN-13 invalid checksum",
            "9780306406158",
            "ISBN-13 check digit is invalid",
        ),
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
            require(
                expected_message in str(exc),
                f"{label}: unexpected error {exc!s}",
            )
        else:
            raise CheckFailure(f"{label}: invalid ISBN was accepted")


async def check_canonicalization_and_duplicates(
    context: RegressionContext,
) -> None:
    context.reset_database()
    product = context.product
    client = context.client
    require(product is not None, "application modules are unavailable")
    require(client is not None, "ASGI client is unavailable")
    first, _second = await create_primary_and_secondary(client)
    first_form = primary_book_form()
    second_form = secondary_book_form()

    for isbn in (
        "03-064-0615-2",
        "9780306406157",
        "978-03-0640615-7",
    ):
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
    lowercase_x_create = await client.request(
        "POST",
        "/books",
        form=lowercase_x_form,
    )
    require(
        lowercase_x_create.status_code == 303,
        "lowercase x ISBN-10 Create did not return 303",
    )
    lowercase_x_path = urlsplit(
        lowercase_x_create.headers.get("location", "")
    ).path
    lowercase_x_id = int(lowercase_x_path.rsplit("/", 1)[1])
    require_stored_isbn(
        product,
        lowercase_x_id,
        "9780804429573",
        "lowercase x ISBN-10 Create",
    )

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
        form={
            **lowercase_x_form,
            "title": "Uppercase X Update",
            "isbn": "080442957X",
        },
    )
    require(
        self_expression_update.status_code == 303,
        "self ISBN expression Update did not return 303",
    )
    require(
        urlsplit(
            self_expression_update.headers.get("location", "")
        ).path
        == lowercase_x_path,
        "self ISBN expression Update redirect differs",
    )
    require_stored_isbn(
        product,
        lowercase_x_id,
        "9780804429573",
        "uppercase X self Update",
    )

    other_book_duplicate = await client.request(
        "POST",
        f"{first.path}/edit",
        form={
            **first_form,
            "title": "Other ISBN Duplicate",
            "isbn": "9780804429573",
        },
    )
    require_field_error(
        other_book_duplicate,
        "isbn",
        "ISBN already exists.",
        "other Book canonical ISBN Update",
    )

    duplicate_979 = await client.request(
        "POST",
        "/books",
        form={
            **first_form,
            "title": "Duplicate 979 ISBN",
            "isbn": second_form["isbn"],
        },
    )
    require_field_error(
        duplicate_979,
        "isbn",
        "ISBN already exists.",
        "duplicate 979 ISBN Create",
    )

    long_isbn = "1-2-3-4-5-6-7-8-90123"
    require(len(long_isbn) == 21, "long ISBN test input is not 21 characters")
    require(
        len(long_isbn.replace("-", "")) == 13,
        "long ISBN test input is not 13 digits",
    )
    invalid_cases = [
        ("blank ISBN", "", "This field is required."),
        (
            "ISBN-10 character in first nine",
            "03064X6152",
            "ISBN-10 must use digits in the first 9 positions",
        ),
        (
            "ISBN-10 invalid final character",
            "030640615A",
            "ISBN contains characters that are not allowed.",
        ),
        (
            "ISBN-10 invalid check digit",
            "0306406153",
            "ISBN-10 check digit is invalid.",
        ),
        (
            "ISBN-13 character",
            "97803064061X7",
            "ISBN-13 must contain digits only.",
        ),
        (
            "ISBN-13 invalid prefix",
            "9770306406158",
            "ISBN-13 must start with 978 or 979.",
        ),
        (
            "ISBN-13 invalid check digit",
            "9780306406158",
            "ISBN-13 check digit is invalid.",
        ),
        (
            "ISBN internal space",
            "978030 6406157",
            "ISBN must not contain whitespace.",
        ),
        (
            "ISBN start hyphen",
            "-0306406152",
            "ISBN must not start with a hyphen.",
        ),
        (
            "ISBN end hyphen",
            "0306406152-",
            "ISBN must not end with a hyphen.",
        ),
        (
            "ISBN double hyphen",
            "0--306406152",
            "ISBN must not contain consecutive hyphens.",
        ),
        ("ISBN character count", "03064061520", "10 or 13 characters"),
        (
            "ISBN over 20 characters",
            long_isbn,
            "ISBN must be at most 20 characters.",
        ),
    ]
    with product.database.SessionLocal() as db:
        book_count_before = db.query(product.Book).count()
    for label, raw_isbn, expected_message in invalid_cases:
        response = await client.request(
            "POST",
            "/books",
            form={
                **first_form,
                "title": f"Invalid {label}",
                "isbn": raw_isbn,
            },
        )
        require_field_error(response, "isbn", expected_message, label)
    with product.database.SessionLocal() as db:
        require(
            db.query(product.Book).count() == book_count_before,
            "invalid ISBN Form stored a Book",
        )
