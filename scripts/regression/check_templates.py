from __future__ import annotations

import re

from scripts.regression.assertions import require
from scripts.regression.context import APPLICATION_DIRECTORY


def check_url_for_source() -> None:
    template_paths = sorted(
        (APPLICATION_DIRECTORY / "templates").glob("*.html")
    )
    template_source = "\n".join(path.read_text() for path in template_paths)
    route_source = (
        APPLICATION_DIRECTORY / "routers" / "book_router.py"
    ).read_text()

    for route_name in (
        "home",
        "book_list",
        "book_new",
        "book_create",
        "book_detail",
        "book_edit",
        "book_update",
        "book_delete",
    ):
        pattern = rf"request\.url_for\(\s*['\"]{route_name}['\"]"
        require(
            re.search(pattern, template_source) is not None,
            f"Template url_for missing: {route_name}",
        )

    require(
        'request.url_for("book_detail"' in route_source,
        "Create/Update redirect does not use the book_detail Route Name",
    )
    require(
        'request.url_for("book_list"' in route_source,
        "Update/Delete redirect does not use the book_list Route Name",
    )
    require(
        "/books/books" not in template_source + route_source,
        "double prefix literal found",
    )
    require('action=""' not in template_source, "empty form action found")
