from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.category import Category


def get_categories(db: Session) -> list[Category]:
    statement = select(Category).order_by(Category.category_name)
    return list(db.scalars(statement).all())


def get_category(db: Session, category_id: int) -> Category | None:
    statement = select(Category).where(Category.category_id == category_id)
    return db.scalar(statement)
