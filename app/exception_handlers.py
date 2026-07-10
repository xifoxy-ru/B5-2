from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import (
    ERROR_TEMPLATE_NAME,
    INVALID_REQUEST_MESSAGE,
    METHOD_NOT_ALLOWED_MESSAGE,
    NOT_FOUND_MESSAGE,
    REQUEST_ERROR_MESSAGE,
)
from app.template_config import templates


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException):
        messages = {
            status.HTTP_404_NOT_FOUND: NOT_FOUND_MESSAGE,
            status.HTTP_405_METHOD_NOT_ALLOWED: METHOD_NOT_ALLOWED_MESSAGE,
        }
        message = messages.get(exc.status_code, REQUEST_ERROR_MESSAGE)
        return _render_error_page(
            request=request,
            status_code=exc.status_code,
            message=message,
        )

    @app.exception_handler(RequestValidationError)
    async def request_validation_exception_handler(request: Request, exc: RequestValidationError):
        return _render_error_page(
            request=request,
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            message=INVALID_REQUEST_MESSAGE,
        )


def _render_error_page(request: Request, status_code: int, message: str):
    return templates.TemplateResponse(
        request,
        ERROR_TEMPLATE_NAME,
        {
            "status_code": status_code,
            "message": message,
            "detail": message,
        },
        status_code=status_code,
    )
