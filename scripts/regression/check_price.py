from __future__ import annotations

from decimal import Decimal
import re
from urllib.parse import urlsplit

from scripts.regression.assertions import (
    field_fragment,
    require,
    require_field_error,
    require_html,
    require_original_input,
)
from scripts.regression.context import APPLICATION_DIRECTORY, RegressionContext
from scripts.regression.fixtures import (
    generated_isbn_13,
    primary_book_form,
    require_stored_price_cents,
)


def check_policy(context: RegressionContext) -> None:
    product = context.product
    require(product is not None, "application modules are unavailable")
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
        actual_cents = product.book_service._parse_required_price_cents(
            raw_price,
            errors,
        )
        require(not errors, f"{raw_price}: valid price produced errors {errors!r}")
        require(
            actual_cents == expected_cents,
            f"{raw_price}: cents differ: {actual_cents!r}",
        )
        require(isinstance(actual_cents, int), f"{raw_price}: cents is not int")
        restored = product.book_service.price_cents_to_decimal(actual_cents)
        require(
            restored == Decimal(expected_cents) / Decimal("100"),
            f"{raw_price}: Decimal restore differs",
        )
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
        (
            "999999999999999.999",
            "Price may have at most 2 decimal places.",
        ),
    ]
    for raw_price, expected_message in invalid_cases:
        errors = {}
        actual_cents = product.book_service._parse_required_price_cents(
            raw_price,
            errors,
        )
        require(
            actual_cents is None,
            f"{raw_price!r}: invalid price produced cents",
        )
        require(
            expected_message in errors.get("price", ""),
            f"{raw_price!r}: unexpected price error {errors!r}",
        )


def check_display(context: RegressionContext) -> None:
    product = context.product
    require(product is not None, "application modules are unavailable")
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
    require(
        product.template_config.format_price(None) == "$0.00",
        "price display fallback differs",
    )


def check_source_structure() -> None:
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
        require(
            re.search(pattern, source) is None,
            f"active price flow still contains {label}",
        )
    require(
        source.count("price_cents") >= 8,
        "price_cents is not connected across active layers",
    )
    list_source = (
        APPLICATION_DIRECTORY / "templates" / "book_list.html"
    ).read_text()
    detail_source = (
        APPLICATION_DIRECTORY / "templates" / "book_detail.html"
    ).read_text()
    form_source = (
        APPLICATION_DIRECTORY / "templates" / "book_form.html"
    ).read_text()
    require(
        "book.price_cents | format_price" in list_source,
        "Book list price Filter differs",
    )
    require(
        "book.price_cents | format_price" in detail_source,
        "Book detail price Filter differs",
    )
    require(
        "format_price" not in form_source,
        "Book Form uses the display price Filter",
    )
    require(
        "price_cents" not in form_source,
        "Book Form exposes cents directly",
    )


async def check_create_update_and_validation(
    context: RegressionContext,
) -> None:
    context.reset_database()
    product = context.product
    client = context.client
    require(product is not None, "application modules are unavailable")
    require(client is not None, "ASGI client is unavailable")
    first_form = primary_book_form()
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
    for sequence, (raw_price, expected_cents) in enumerate(
        price_cases,
        start=1,
    ):
        price_form = {
            **first_form,
            "title": f"Price Flow {sequence}",
            "price": raw_price,
            "isbn": generated_isbn_13(sequence),
        }
        created = await client.request("POST", "/books", form=price_form)
        require(
            created.status_code == 303,
            f"price Create {raw_price!r} did not return 303",
        )
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
        expected_display = (
            f"${Decimal(expected_cents) / Decimal('100'):,.2f}"
        )
        require(
            expected_display in detail.body,
            f"price Detail {raw_price!r} missing {expected_display!r}",
        )

        edit_form = await client.request("GET", f"{created_path}/edit")
        require_html(edit_form, 200, f"price Edit Form {raw_price!r}")
        expected_form_value = format(
            Decimal(expected_cents) / Decimal("100"),
            ".2f",
        )
        require_original_input(
            edit_form,
            "price",
            expected_form_value,
            f"restored price Form {raw_price!r}",
        )
        price_fragment = field_fragment(edit_form.body, "price")
        require(
            "$" not in price_fragment,
            f"price Edit Form {raw_price!r} contains currency symbol",
        )
        require(
            "," not in price_fragment,
            f"price Edit Form {raw_price!r} contains comma",
        )

        updated = await client.request(
            "POST",
            f"{created_path}/edit",
            form={**price_form, "title": f"Price Updated {sequence}"},
        )
        require(
            updated.status_code == 303,
            f"price Update {raw_price!r} did not return 303",
        )
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
        require(
            expected_display in price_list.body,
            f"Book list missing price display {expected_display!r}",
        )
    require(
        "$1,050.00" not in price_list.body,
        "1050 cents was displayed as $1,050.00",
    )
    require(
        ">1050<" not in price_list.body,
        "raw 1050 cents was displayed in Book list",
    )
    require(
        ">1,050.00<" not in price_list.body,
        "Book list displayed price without currency symbol",
    )

    invalid_cases = [
        ("blank price", "", "This field is required."),
        ("whitespace price", "   ", "This field is required."),
        ("negative zero price", "-0", "Price must be 0 or greater."),
        (
            "negative zero decimal price",
            "-0.00",
            "Price must be 0 or greater.",
        ),
        ("negative integer price", "-1", "Price must be 0 or greater."),
        ("negative decimal price", "-0.01", "Price must be 0 or greater."),
        ("plus price", "+10", "Price must be a decimal amount"),
        ("alphabetic price", "abc", "Price must be a decimal amount"),
        ("mixed price", "10a", "Price must be a decimal amount"),
        ("currency-symbol price", "$10", "Price must be a decimal amount"),
        ("comma price", "1,000", "Price must be a decimal amount"),
        (
            "trailing decimal point price",
            "10.",
            "Price must be a decimal amount",
        ),
        (
            "leading decimal point price",
            ".50",
            "Price must be a decimal amount",
        ),
        (
            "three zero decimal price",
            "10.000",
            "Price may have at most 2 decimal places.",
        ),
        (
            "three decimal price",
            "10.123",
            "Price may have at most 2 decimal places.",
        ),
        ("lowercase exponent price", "1e3", "Price must be a decimal amount"),
        ("uppercase exponent price", "1E3", "Price must be a decimal amount"),
        ("NaN price", "NaN", "Price must be a finite number."),
        ("Infinity price", "Infinity", "Price must be a finite number."),
        ("-Infinity price", "-Infinity", "Price must be a finite number."),
        (
            "integer price above maximum",
            "1000000000000000",
            "Price must be at most",
        ),
        (
            "decimal price above maximum",
            "1000000000000000.00",
            "Price must be at most",
        ),
        (
            "maximum price with excess scale",
            "999999999999999.999",
            "Price may have at most 2 decimal places.",
        ),
    ]
    with product.database.SessionLocal() as db:
        book_count_before = db.query(product.Book).count()
    for label, raw_price, expected_message in invalid_cases:
        response = await client.request(
            "POST",
            "/books",
            form={
                **first_form,
                "title": f"Invalid {label}",
                "price": raw_price,
            },
        )
        require_field_error(response, "price", expected_message, label)
        require_original_input(response, "price", raw_price, label)
    with product.database.SessionLocal() as db:
        require(
            db.query(product.Book).count() == book_count_before,
            "invalid price Form stored a Book",
        )
