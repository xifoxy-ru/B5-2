from typing import Any

from fastapi.templating import Jinja2Templates

from app.config import TEMPLATES_DIRECTORY


def format_price(value: Any) -> str:
    try:
        return f"{float(value):,.2f}"
    except (TypeError, ValueError):
        return "0.00"


templates = Jinja2Templates(directory=TEMPLATES_DIRECTORY)
templates.env.filters["format_price"] = format_price
