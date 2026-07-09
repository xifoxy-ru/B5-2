from collections.abc import Generator

from sqlalchemy import create_engine, select
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

DATABASE_URL = "sqlite:///./library.db"

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    import app.models.author
    import app.models.book
    import app.models.category

    Base.metadata.create_all(bind=engine)


def seed_reference_data() -> None:
    from app.models.author import Author
    from app.models.category import Category

    authors = [
        {"author_name": "Jane Austen", "nationality": "British"},
        {"author_name": "George Orwell", "nationality": "British"},
        {"author_name": "Han Kang", "nationality": "Korean"},
        {"author_name": "Isaac Asimov", "nationality": "American"},
        {"author_name": "Yuval Noah Harari", "nationality": "Israeli"},
    ]
    categories = [
        {"category_name": "Fiction", "description": "Novels and literary fiction"},
        {"category_name": "Science Fiction", "description": "Speculative and futuristic works"},
        {"category_name": "History", "description": "Historical research and narratives"},
        {"category_name": "Essay", "description": "Essay collections and criticism"},
        {"category_name": "Computer Science", "description": "Programming and computing books"},
    ]

    with SessionLocal() as db:
        existing_author_names = set(db.scalars(select(Author.author_name)).all())
        existing_category_names = set(db.scalars(select(Category.category_name)).all())

        for author_data in authors:
            if author_data["author_name"] not in existing_author_names:
                db.add(Author(**author_data))

        for category_data in categories:
            if category_data["category_name"] not in existing_category_names:
                db.add(Category(**category_data))

        db.commit()
