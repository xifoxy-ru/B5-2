from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.database import init_db, seed_reference_data
from app.routers.book_router import router as book_router


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    init_db()
    seed_reference_data()
    yield


app = FastAPI(title="B5-2 Book CRUD", lifespan=lifespan)

app.include_router(book_router)
