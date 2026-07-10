from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import APP_TITLE
from app.database import init_db, seed_reference_data
from app.exception_handlers import register_exception_handlers
from app.routers.book_router import router as book_router


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    init_db()
    seed_reference_data()
    yield


def create_app() -> FastAPI:
    app = FastAPI(title=APP_TITLE, lifespan=lifespan)
    app.include_router(book_router)
    register_exception_handlers(app)
    return app


app = create_app()
