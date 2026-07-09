from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.author import Author


def get_authors(db: Session) -> list[Author]:
    statement = select(Author).order_by(Author.author_name)
    return list(db.scalars(statement).all())


def get_author(db: Session, author_id: int) -> Author | None:
    statement = select(Author).where(Author.author_id == author_id)
    return db.scalar(statement)
