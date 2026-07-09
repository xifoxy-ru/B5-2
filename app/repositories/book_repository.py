from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.models.book import Book


def get_books(db: Session, q: str | None = None) -> list[Book]:
    statement = (
        select(Book)
        .options(joinedload(Book.author), joinedload(Book.category))
        .order_by(Book.book_id)
    )

    if q and q.strip():
        statement = statement.where(Book.title.contains(q.strip()))

    return list(db.scalars(statement).all())


def get_book(db: Session, book_id: int) -> Book | None:
    statement = (
        select(Book)
        .options(joinedload(Book.author), joinedload(Book.category))
        .where(Book.book_id == book_id)
    )
    return db.scalar(statement)


def get_book_by_isbn(db: Session, isbn: str) -> Book | None:
    statement = select(Book).where(Book.isbn == isbn)
    return db.scalar(statement)


def create_book(
    db: Session,
    title: str,
    author_id: int,
    category_id: int,
    published_year: int,
    price: float,
    stock_quantity: int,
    isbn: str,
) -> Book:
    book = Book(
        title=title,
        author_id=author_id,
        category_id=category_id,
        published_year=published_year,
        price=price,
        stock_quantity=stock_quantity,
        isbn=isbn,
    )
    db.add(book)
    db.commit()
    db.refresh(book)
    return book


def update_book(
    db: Session,
    book: Book,
    title: str,
    author_id: int,
    category_id: int,
    published_year: int,
    price: float,
    stock_quantity: int,
    isbn: str,
) -> Book:
    book.title = title
    book.author_id = author_id
    book.category_id = category_id
    book.published_year = published_year
    book.price = price
    book.stock_quantity = stock_quantity
    book.isbn = isbn
    db.commit()
    db.refresh(book)
    return book


def delete_book(db: Session, book: Book) -> None:
    db.delete(book)
    db.commit()
