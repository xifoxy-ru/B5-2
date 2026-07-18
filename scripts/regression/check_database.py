from __future__ import annotations

from decimal import Decimal
import sys

from sqlalchemy import BigInteger, String, event, text
from sqlalchemy.exc import IntegrityError

from scripts.regression.assertions import CheckFailure, require
from scripts.regression.context import APPLICATION_DIRECTORY, RegressionContext


def check_model_contracts(context: RegressionContext) -> None:
    product = context.product
    require(product is not None, "application modules are unavailable")
    require(context.original_engine is not None, "original Engine is unavailable")
    require(product.policy.MAX_TITLE_LENGTH == 200, "title maximum is not 200")
    require(product.policy.MAX_AUTHOR_LENGTH == 100, "author maximum is not 100")
    require(
        product.policy.MAX_CATEGORY_LENGTH == 100,
        "category maximum is not 100",
    )
    require(product.policy.MAX_ISBN_LENGTH == 20, "ISBN maximum is not 20")
    require(
        sorted(product.database.Base.metadata.tables) == ["books"],
        (
            "unexpected metadata tables: "
            f"{sorted(product.database.Base.metadata.tables)!r}"
        ),
    )
    require(
        "app.models.author" not in sys.modules,
        "Author Model was imported into the product flow",
    )
    require(
        "app.models.category" not in sys.modules,
        "Category Model was imported into the product flow",
    )
    require(
        [column.key for column in product.Book.__table__.columns]
        == [
            "book_id",
            "title",
            "author",
            "category",
            "published_year",
            "price_cents",
            "stock_quantity",
            "isbn",
        ],
        "Book columns differ from the single-model contract",
    )
    require(not product.Book.__table__.foreign_keys, "Book still has Foreign Keys")
    require(
        not product.Book.__mapper__.relationships,
        "Book still has ORM relationships",
    )
    require("author_id" not in product.Book.__table__.c, "Book.author_id still exists")
    require(
        "category_id" not in product.Book.__table__.c,
        "Book.category_id still exists",
    )
    require(
        isinstance(product.Book.__table__.c.author.type, String),
        "Book.author is not a String",
    )
    require(
        isinstance(product.Book.__table__.c.category.type, String),
        "Book.category is not a String",
    )
    require(
        product.Book.__table__.c.author.type.length == 100,
        "Book.author length differs",
    )
    require(
        product.Book.__table__.c.category.type.length == 100,
        "Book.category length differs",
    )
    require(
        product.Book.__table__.c.title.type.length == 200,
        "Book.title model length differs",
    )
    require(
        product.Book.__table__.c.isbn.type.length == 20,
        "Book.isbn model length differs",
    )
    require(
        "price" not in product.Book.__table__.c,
        "legacy Book.price Column still exists",
    )
    require(
        isinstance(product.Book.__table__.c.price_cents.type, BigInteger),
        "Book.price_cents is not BigInteger",
    )
    require(
        product.policy.MAX_PRICE == Decimal("999999999999999.99"),
        "price maximum differs",
    )
    require(
        product.policy.MAX_PRICE_INTEGER_DIGITS == 15,
        "price integer digit maximum differs",
    )
    require(
        product.policy.MAX_PRICE_DECIMAL_PLACES == 2,
        "price decimal place maximum differs",
    )
    require(
        product.policy.MAX_PRICE_CENTS == 99_999_999_999_999_999,
        "maximum cents differs",
    )
    require(
        product.policy.MAX_PRICE_CENTS
        <= product.policy.SQLITE_SIGNED_64_MAX,
        "maximum cents exceeds SQLite signed 64-bit INTEGER",
    )
    require(
        event.contains(
            context.original_engine,
            "connect",
            product.database._enable_sqlite_foreign_keys,
        ),
        "application SQLite Engine listener is not registered",
    )


def check_single_model_source() -> None:
    removed_paths = [
        APPLICATION_DIRECTORY / "models" / "author.py",
        APPLICATION_DIRECTORY / "models" / "category.py",
        APPLICATION_DIRECTORY / "repositories" / "author_repository.py",
        APPLICATION_DIRECTORY / "repositories" / "category_repository.py",
    ]
    for removed_path in removed_paths:
        require(
            not removed_path.exists(),
            f"legacy Author/Category file remains: {removed_path}",
        )

    active_paths = [
        APPLICATION_DIRECTORY / "database.py",
        APPLICATION_DIRECTORY / "main.py",
        APPLICATION_DIRECTORY / "models" / "__init__.py",
        APPLICATION_DIRECTORY / "models" / "book.py",
        APPLICATION_DIRECTORY / "repositories" / "book_repository.py",
        APPLICATION_DIRECTORY / "routers" / "book_router.py",
        APPLICATION_DIRECTORY / "schemas" / "book.py",
        APPLICATION_DIRECTORY / "services" / "book_service.py",
        APPLICATION_DIRECTORY / "templates" / "book_form.html",
        APPLICATION_DIRECTORY / "templates" / "book_list.html",
        APPLICATION_DIRECTORY / "templates" / "book_detail.html",
    ]
    source = "\n".join(path.read_text() for path in active_paths)
    forbidden_tokens = (
        "author_id",
        "category_id",
        "ForeignKey(",
        "relationship(",
        "joinedload(",
        "author_repository",
        "category_repository",
        "seed_reference_data",
        "book.author.",
        "book.category.",
    )
    for token in forbidden_tokens:
        require(
            token not in source,
            f"active Book flow still contains {token!r}",
        )


def check_database_behavior(context: RegressionContext) -> None:
    context.reset_database()
    product = context.product
    temporary_engine = context.temporary_engine
    require(product is not None, "application modules are unavailable")
    require(temporary_engine is not None, "temporary Engine is unavailable")

    with temporary_engine.connect() as connection:
        pragma_value = connection.scalar(text("PRAGMA foreign_keys"))
        table_names = list(
            connection.scalars(
                text(
                    "SELECT name FROM sqlite_master "
                    "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' "
                    "ORDER BY name"
                )
            ).all()
        )
        table_info = connection.execute(text("PRAGMA table_info(books)")).all()
        foreign_keys = connection.execute(
            text("PRAGMA foreign_key_list(books)")
        ).all()
        indexes = connection.execute(text("PRAGMA index_list(books)")).all()

    require(pragma_value == 1, f"PRAGMA foreign_keys expected 1, got {pragma_value}")
    require(table_names == ["books"], f"unexpected user tables: {table_names!r}")
    column_types = {row[1]: row[2] for row in table_info}
    require(
        list(column_types)
        == [
            "book_id",
            "title",
            "author",
            "category",
            "published_year",
            "price_cents",
            "stock_quantity",
            "isbn",
        ],
        f"books columns differ: {list(column_types)!r}",
    )
    require(column_types["author"] == "VARCHAR(100)", "books.author DB type differs")
    require(
        column_types["category"] == "VARCHAR(100)",
        "books.category DB type differs",
    )
    require(
        "price" not in column_types,
        "legacy books.price Column still exists",
    )
    require(
        "INT" in column_types["price_cents"],
        "books.price_cents is not INTEGER-compatible",
    )
    require(not foreign_keys, f"books still has Foreign Keys: {foreign_keys!r}")
    require(
        any(row[2] == 1 for row in indexes),
        "books ISBN Unique index is missing",
    )

    with product.database.SessionLocal() as db:
        valid_book = product.Book(
            title="Direct Single Model",
            author="Direct Author",
            category="Direct Category",
            published_year=2025,
            price_cents=1000,
            stock_quantity=1,
            isbn="9780000000019",
        )
        db.add(valid_book)
        db.commit()
        require(valid_book.book_id is not None, "single-model direct insert failed")
        require(
            valid_book.author == "Direct Author",
            "direct author string was not stored",
        )
        require(
            valid_book.category == "Direct Category",
            "direct category string was not stored",
        )
        require(
            valid_book.price_cents == 1000,
            "direct cents value was not stored",
        )
        require(
            isinstance(valid_book.price_cents, int),
            "Book.price_cents ORM value is not int",
        )

        db.add(
            product.Book(
                title="Duplicate Direct ISBN",
                author="Other Author",
                category="Other Category",
                published_year=2025,
                price_cents=1000,
                stock_quantity=1,
                isbn=valid_book.isbn,
            )
        )
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
        else:
            raise CheckFailure(
                "ISBN Unique constraint did not reject a duplicate"
            )

        maximum_price_book = product.Book(
            title="Maximum Cents",
            author="Maximum Author",
            category="Maximum Category",
            published_year=2025,
            price_cents=product.policy.MAX_PRICE_CENTS,
            stock_quantity=1,
            isbn="9780000000040",
        )
        db.add(maximum_price_book)
        db.commit()
        db.refresh(maximum_price_book)
        require(
            maximum_price_book.book_id is not None,
            "valid insert after rollback failed",
        )
        require(
            maximum_price_book.price_cents
            == 99_999_999_999_999_999,
            "maximum cents did not round-trip exactly",
        )
        require(
            isinstance(maximum_price_book.price_cents, int),
            "maximum ORM cents is not int",
        )
        raw_cents, storage_type = db.execute(
            text(
                "SELECT price_cents, typeof(price_cents) "
                "FROM books WHERE book_id = :book_id"
            ),
            {"book_id": maximum_price_book.book_id},
        ).one()
        require(
            raw_cents == 99_999_999_999_999_999,
            "raw SQLite maximum cents differs",
        )
        require(
            storage_type == "integer",
            f"raw SQLite cents storage type differs: {storage_type!r}",
        )
