from fastapi import APIRouter

from app.routers.book_router import router as book_router
from app.routers.home_router import router as home_router


router = APIRouter()
router.include_router(home_router)
router.include_router(book_router)
