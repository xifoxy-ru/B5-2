from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.database import init_db, seed_reference_data
from app.routers.book_router import router as book_router

templates = Jinja2Templates(directory="app/templates")


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    init_db()
    seed_reference_data()
    yield


app = FastAPI(title="B5-2 Book CRUD", lifespan=lifespan)

app.include_router(book_router)


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    messages = {
        404: "Page not found.",
        405: "Method not allowed.",
    }
    message = messages.get(exc.status_code, "Request error.")
    return templates.TemplateResponse(
        request,
        "error.html",
        {
            "status_code": exc.status_code,
            "message": message,
            "detail": message,
        },
        status_code=exc.status_code,
    )


@app.exception_handler(RequestValidationError)
async def request_validation_exception_handler(request: Request, exc: RequestValidationError):
    return templates.TemplateResponse(
        request,
        "error.html",
        {
            "status_code": 422,
            "message": "Invalid request.",
            "detail": "Invalid request.",
        },
        status_code=422,
    )
