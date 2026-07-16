from decimal import Decimal
from typing import Any

from fastapi.templating import Jinja2Templates

from app.config import TEMPLATES_DIRECTORY
from app.services.book_validation_policy import PRICE_CENTS_PER_UNIT


def format_price(value: Any) -> str:
    try:
        price = Decimal(value) / PRICE_CENTS_PER_UNIT
        return f"${price:,.2f}"
    except (TypeError, ValueError, ArithmeticError):
        return "$0.00"


templates = Jinja2Templates(directory=TEMPLATES_DIRECTORY)
templates.env.filters["format_price"] = format_price
