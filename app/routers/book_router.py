from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.config import BOOK_NOT_FOUND_MESSAGE, ERROR_TEMPLATE_NAME
from app.database import get_db
from app.models.book import Book
from app.schemas.book import BookFormData
from app.services import book_service
from app.services.book_service import BookValidationError
from app.services.book_validation_policy import (
    MAX_FUTURE_PUBLICATION_YEAR,
    MAX_ISBN_LENGTH,
    MAX_PRICE,
    MAX_STOCK_QUANTITY,
    MAX_TITLE_LENGTH,
    MIN_PRICE,
    MIN_PUBLISHED_YEAR,
    MIN_STOCK_QUANTITY,
)
from app.template_config import templates


router = APIRouter(
    prefix="/books",
    tags=["books"],
)

BOOK_FORM_VALIDATION_LIMITS = {
    "max_title_length": MAX_TITLE_LENGTH,
    "min_published_year": MIN_PUBLISHED_YEAR,
    "max_published_year": MAX_FUTURE_PUBLICATION_YEAR,
    "min_price": MIN_PRICE,
    "max_price": MAX_PRICE,
    "min_stock_quantity": MIN_STOCK_QUANTITY,
    "max_stock_quantity": MAX_STOCK_QUANTITY,
    "max_isbn_length": MAX_ISBN_LENGTH,
}


def _book_form_data(
    title: Annotated[str, Form()] = "",
    author_id: Annotated[str, Form()] = "",
    category_id: Annotated[str, Form()] = "",
    published_year: Annotated[str, Form()] = "",
    price: Annotated[str, Form()] = "",
    stock_quantity: Annotated[str, Form()] = "",
    isbn: Annotated[str, Form()] = "",
) -> BookFormData:
    return BookFormData(
        title=title,
        author_id=author_id,
        category_id=category_id,
        published_year=published_year,
        price=price,
        stock_quantity=stock_quantity,
        isbn=isbn,
    )


@router.get("", name="book_list")
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


@router.get("/new", name="book_new")
def new_book_form(request: Request, db: Session = Depends(get_db)):
    options = book_service.get_book_form_options(db)
    return templates.TemplateResponse(
        request,
        "book_form.html",
        {
            **options,
            "mode": "create",
            "form_data": _empty_book_form_data(),
            "errors": {},
            "validation_limits": BOOK_FORM_VALIDATION_LIMITS,
        },
    )


@router.post("", name="book_create")
def create_book(
    request: Request,
    form_data: BookFormData = Depends(_book_form_data),
    db: Session = Depends(get_db),
):
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
                "form_data": form_data.to_form_data(),
                "errors": exc.errors,
                "validation_limits": BOOK_FORM_VALIDATION_LIMITS,
            },
        )

    detail_url = request.url_for("book_detail", book_id=str(created_book.book_id))
    return RedirectResponse(detail_url.path, status_code=status.HTTP_303_SEE_OTHER)


@router.get("/{book_id}", name="book_detail")
def book_detail(
    request: Request,
    book_id: int,
    db: Session = Depends(get_db),
):
    book = book_service.get_book_detail(db, book_id)
    if book is None:
        return _book_not_found_response(request)

    return templates.TemplateResponse(
        request,
        "book_detail.html",
        {
            "book": book,
        },
    )


@router.get("/{book_id}/edit", name="book_edit")
def edit_book_form(
    request: Request,
    book_id: int,
    db: Session = Depends(get_db),
):
    book = book_service.get_book_detail(db, book_id)
    if book is None:
        return _book_not_found_response(request)

    options = book_service.get_book_form_options(db)
    return templates.TemplateResponse(
        request,
        "book_form.html",
        {
            **options,
            "mode": "edit",
            "book_id": book_id,
            "form_data": _book_to_form_data(book),
            "errors": {},
            "validation_limits": BOOK_FORM_VALIDATION_LIMITS,
        },
    )


@router.post("/{book_id}/edit", name="book_update")
def update_book(
    request: Request,
    book_id: int,
    form_data: BookFormData = Depends(_book_form_data),
    db: Session = Depends(get_db),
):
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
                "form_data": form_data.to_form_data(),
                "errors": exc.errors,
                "validation_limits": BOOK_FORM_VALIDATION_LIMITS,
            },
        )

    if updated_book is None:
        list_url = request.url_for("book_list")
        return RedirectResponse(list_url.path, status_code=status.HTTP_303_SEE_OTHER)

    detail_url = request.url_for("book_detail", book_id=str(updated_book.book_id))
    return RedirectResponse(detail_url.path, status_code=status.HTTP_303_SEE_OTHER)


@router.post("/{book_id}/delete", name="book_delete")
def delete_book(request: Request, book_id: int, db: Session = Depends(get_db)):
    book_service.delete_book(db, book_id)
    list_url = request.url_for("book_list")
    return RedirectResponse(list_url.path, status_code=status.HTTP_303_SEE_OTHER)


def _book_not_found_response(request: Request):
    return templates.TemplateResponse(
        request,
        ERROR_TEMPLATE_NAME,
        {
            "status_code": status.HTTP_404_NOT_FOUND,
            "message": BOOK_NOT_FOUND_MESSAGE,
            "detail": BOOK_NOT_FOUND_MESSAGE,
        },
        status_code=status.HTTP_404_NOT_FOUND,
    )


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
