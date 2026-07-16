from dataclasses import dataclass


@dataclass(frozen=True)
class BookFormData:
    title: str
    author: str
    category: str
    published_year: str
    price: str
    stock_quantity: str
    isbn: str

    def to_form_data(self) -> dict[str, str]:
        return {
            "title": self.title,
            "author": self.author,
            "category": self.category,
            "published_year": self.published_year,
            "price": self.price,
            "stock_quantity": self.stock_quantity,
            "isbn": self.isbn,
        }
