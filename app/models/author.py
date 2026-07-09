from __future__ import annotations

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Author(Base):
    __tablename__ = "authors"

    author_id: Mapped[int] = mapped_column(primary_key=True, index=True)
    author_name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    nationality: Mapped[str | None] = mapped_column(String(100), nullable=True)

    books: Mapped[list["Book"]] = relationship(back_populates="author")
