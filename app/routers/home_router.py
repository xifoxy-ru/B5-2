from fastapi import APIRouter, Request

from app.config import APP_TITLE
from app.template_config import templates


router = APIRouter()


@router.get("/", name="home")
def home(request: Request):
    return templates.TemplateResponse(
        request,
        "home.html",
        {
            "title": APP_TITLE,
            "description": "Server-rendered Book CRUD application",
        },
    )
