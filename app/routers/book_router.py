from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.book import Book
from app.services import book_service
from app.services.book_service import BookValidationError, MAX_FUTURE_PUBLICATION_YEAR

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


def format_price(value: Any) -> str:
    try:
        return f"{float(value):,.2f}"
    except (TypeError, ValueError):
        return "0.00"


templates.env.filters["format_price"] = format_price


@router.get("/")
def home(request: Request):
    return templates.TemplateResponse(
        request,
        "home.html",
        {
            "title": "B5-2 Book CRUD",
            "description": "Server-rendered Book CRUD application",
        },
    )


@router.get("/books")
def list_books(
    request: Request,
    q: str | None = None,
    db: Session = Depends(get_db),
):
    books = book_service.list_books(db, q=q)
    return templates.TemplateResponse(
        request,
        "book_list.html",
        {
            "books": books,
            "q": q or "",
        },
    )


@router.get("/books/new")
def new_book_form(request: Request, db: Session = Depends(get_db)):
    options = book_service.get_book_form_options(db)
    return templates.TemplateResponse(
        request,
        "book_form.html",
        {
            **options,
            "mode": "create",
            "action_url": "/books",
            "form_data": _empty_book_form_data(),
            "errors": {},
            "current_year": MAX_FUTURE_PUBLICATION_YEAR,
        },
    )


@router.post("/books")
def create_book(
    request: Request,
    title: Annotated[str, Form()] = "",
    author_id: Annotated[str, Form()] = "",
    category_id: Annotated[str, Form()] = "",
    published_year: Annotated[str, Form()] = "",
    price: Annotated[str, Form()] = "",
    stock_quantity: Annotated[str, Form()] = "",
    isbn: Annotated[str, Form()] = "",
    db: Session = Depends(get_db),
):
    form_data = _book_form_data(
        title=title,
        author_id=author_id,
        category_id=category_id,
        published_year=published_year,
        price=price,
        stock_quantity=stock_quantity,
        isbn=isbn,
    )

    try:
        created_book = book_service.create_book(db, form_data)
    except BookValidationError as exc:
        options = book_service.get_book_form_options(db)
        return templates.TemplateResponse(
            request,
            "book_form.html",
            {
                **options,
                "mode": "create",
                "action_url": "/books",
                "form_data": form_data,
                "errors": exc.errors,
                "current_year": MAX_FUTURE_PUBLICATION_YEAR,
            },
        )

    return RedirectResponse(f"/books/{created_book.book_id}", status_code=303)


@router.get("/books/{book_id}")
def book_detail(
    request: Request,
    book_id: int,
    db: Session = Depends(get_db),
):
    book = book_service.get_book_detail(db, book_id)
    if book is None:
        return RedirectResponse("/books", status_code=303)

    return templates.TemplateResponse(
        request,
        "book_detail.html",
        {
            "book": book,
        },
    )


@router.get("/books/{book_id}/edit")
def edit_book_form(
    request: Request,
    book_id: int,
    db: Session = Depends(get_db),
):
    book = book_service.get_book_detail(db, book_id)
    if book is None:
        return RedirectResponse("/books", status_code=303)

    options = book_service.get_book_form_options(db)
    return templates.TemplateResponse(
        request,
        "book_form.html",
        {
            **options,
            "mode": "edit",
            "book_id": book_id,
            "action_url": f"/books/{book_id}/edit",
            "form_data": _book_to_form_data(book),
            "errors": {},
            "current_year": MAX_FUTURE_PUBLICATION_YEAR,
        },
    )


@router.post("/books/{book_id}/edit")
def update_book(
    request: Request,
    book_id: int,
    title: Annotated[str, Form()] = "",
    author_id: Annotated[str, Form()] = "",
    category_id: Annotated[str, Form()] = "",
    published_year: Annotated[str, Form()] = "",
    price: Annotated[str, Form()] = "",
    stock_quantity: Annotated[str, Form()] = "",
    isbn: Annotated[str, Form()] = "",
    db: Session = Depends(get_db),
):
    form_data = _book_form_data(
        title=title,
        author_id=author_id,
        category_id=category_id,
        published_year=published_year,
        price=price,
        stock_quantity=stock_quantity,
        isbn=isbn,
    )

    try:
        updated_book = book_service.update_book(db, book_id, form_data)
    except BookValidationError as exc:
        options = book_service.get_book_form_options(db)
        return templates.TemplateResponse(
            request,
            "book_form.html",
            {
                **options,
                "mode": "edit",
                "book_id": book_id,
                "action_url": f"/books/{book_id}/edit",
                "form_data": form_data,
                "errors": exc.errors,
                "current_year": MAX_FUTURE_PUBLICATION_YEAR,
            },
        )

    if updated_book is None:
        return RedirectResponse("/books", status_code=303)

    return RedirectResponse(f"/books/{updated_book.book_id}", status_code=303)


@router.post("/books/{book_id}/delete")
def delete_book(book_id: int, db: Session = Depends(get_db)):
    book_service.delete_book(db, book_id)
    return RedirectResponse("/books", status_code=303)


def _empty_book_form_data() -> dict[str, str]:
    return {
        "title": "",
        "author_id": "",
        "category_id": "",
        "published_year": "",
        "price": "",
        "stock_quantity": "1",
        "isbn": "",
    }


def _book_form_data(
    title: str,
    author_id: str,
    category_id: str,
    published_year: str,
    price: str,
    stock_quantity: str,
    isbn: str,
) -> dict[str, str]:
    return {
        "title": title,
        "author_id": author_id,
        "category_id": category_id,
        "published_year": published_year,
        "price": price,
        "stock_quantity": stock_quantity,
        "isbn": isbn,
    }


def _book_to_form_data(book: Book) -> dict[str, Any]:
    return {
        "title": book.title,
        "author_id": book.author_id,
        "category_id": book.category_id,
        "published_year": book.published_year,
        "price": book.price,
        "stock_quantity": book.stock_quantity,
        "isbn": book.isbn,
    }
