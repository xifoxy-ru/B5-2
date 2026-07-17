from __future__ import annotations

from scripts.regression.assertions import (
    require,
    require_field_error,
    require_original_input,
)
from scripts.regression.context import RegressionContext
from scripts.regression.fixtures import require_form_context_preserved


async def check_form_validation(context: RegressionContext) -> None:
    context.reset_database()
    product = context.product
    client = context.client
    require(product is not None, "application modules are unavailable")
    require(client is not None, "ASGI client is unavailable")
    valid_form = {
        "title": "Validation Base",
        "author": "Validation Author",
        "category": "Validation Category",
        "published_year": "2025",
        "price": "10.00",
        "stock_quantity": "1",
        "isbn": "9781234567897",
    }
    with product.database.SessionLocal() as db:
        book_count_before = db.query(product.Book).count()

    cases = [
        ("blank title", {"title": ""}, "title", "This field is required."),
        (
            "long title",
            {"title": "x" * 201},
            "title",
            "Title must be at most 200 characters.",
        ),
        ("blank Author", {"author": ""}, "author", "This field is required."),
        ("blank Category", {"category": ""}, "category", "This field is required."),
        (
            "whitespace Author",
            {"author": "   "},
            "author",
            "This field is required.",
        ),
        (
            "whitespace Category",
            {"category": "   "},
            "category",
            "This field is required.",
        ),
        (
            "long Author",
            {"author": "a" * 101},
            "author",
            "Author must be at most 100 characters.",
        ),
        (
            "long Category",
            {"category": "c" * 101},
            "category",
            "Category must be at most 100 characters.",
        ),
        (
            "invalid year",
            {"published_year": "year"},
            "published_year",
            "This field must be an integer.",
        ),
        (
            "year below range",
            {"published_year": str(product.policy.MIN_PUBLISHED_YEAR - 1)},
            "published_year",
            "Published year must be between",
        ),
        (
            "year above range",
            {
                "published_year": str(
                    product.policy.MAX_FUTURE_PUBLICATION_YEAR + 1
                )
            },
            "published_year",
            "Published year must be between",
        ),
        (
            "invalid stock",
            {"stock_quantity": "stock"},
            "stock_quantity",
            "This field must be an integer.",
        ),
        (
            "negative stock",
            {"stock_quantity": "-1"},
            "stock_quantity",
            "Stock quantity must be between",
        ),
        (
            "stock above maximum",
            {
                "stock_quantity": str(
                    product.policy.MAX_STOCK_QUANTITY + 1
                )
            },
            "stock_quantity",
            "Stock quantity must be between",
        ),
    ]

    for label, overrides, field_name, expected_message in cases:
        response = await client.request(
            "POST",
            "/books",
            form={**valid_form, **overrides},
        )
        require_field_error(response, field_name, expected_message, label)

    missing_stock_form = dict(valid_form)
    missing_stock_form.pop("stock_quantity")
    missing_stock = await client.request(
        "POST",
        "/books",
        form=missing_stock_form,
    )
    require_field_error(
        missing_stock,
        "stock_quantity",
        "This field is required.",
        "missing stock field",
    )

    preserved_create = {
        **valid_form,
        "title": "  Create   Original  ",
        "author": "   ",
        "category": "c" * 101,
        "price": "00010.50",
        "isbn": "bad create isbn",
    }
    preserved_response = await client.request(
        "POST",
        "/books",
        form=preserved_create,
    )
    require_field_error(
        preserved_response,
        "author",
        "This field is required.",
        "preserved Create author",
    )
    require_field_error(
        preserved_response,
        "category",
        "Category must be at most 100 characters.",
        "preserved Create category",
    )
    for field_name in ("title", "author", "category", "price", "isbn"):
        require_original_input(
            preserved_response,
            field_name,
            preserved_create[field_name],
            f"Create {field_name}",
        )
    require_form_context_preserved(
        product,
        book_id=None,
        form=preserved_create,
    )

    with product.database.SessionLocal() as db:
        require(
            db.query(product.Book).count() == book_count_before,
            "invalid general Form stored a Book",
        )
