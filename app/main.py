from fastapi import FastAPI

from app.routers.book_router import router as book_router

app = FastAPI(title="B5-2 Book CRUD")

app.include_router(book_router)
