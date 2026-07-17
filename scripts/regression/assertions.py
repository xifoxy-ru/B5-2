from __future__ import annotations

from html import escape
from html.parser import HTMLParser
import sys
from typing import Any, Callable, Coroutine, Protocol
from urllib.parse import urlsplit


class CheckFailure(AssertionError):
    pass


class StopRegressionChecks(Exception):
    pass


class ResponseLike(Protocol):
    status_code: int
    headers: dict[str, str]
    body: str


class HTMLTargets(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[str] = []
        self.form_actions: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "a" and attributes.get("href") is not None:
            self.links.append(attributes["href"] or "")
        if tag == "form" and attributes.get("action") is not None:
            self.form_actions.append(attributes["action"] or "")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise CheckFailure(message)


def run_check(name: str, operation: Callable[[], Any]) -> Any:
    try:
        result = operation()
    except Exception as exc:
        detail = str(exc) or type(exc).__name__
        print(f"[FAIL] {name}: {detail}", file=sys.stderr)
        raise StopRegressionChecks from exc
    print(f"[PASS] {name}")
    return result


async def run_async_check(
    name: str,
    operation: Callable[[], Coroutine[Any, Any, Any]],
) -> Any:
    try:
        result = await operation()
    except Exception as exc:
        detail = str(exc) or type(exc).__name__
        print(f"[FAIL] {name}: {detail}", file=sys.stderr)
        raise StopRegressionChecks from exc
    print(f"[PASS] {name}")
    return result


def html_targets(body: str) -> HTMLTargets:
    targets = HTMLTargets()
    targets.feed(body)
    return targets


def target_paths(targets: list[str]) -> list[str]:
    return [urlsplit(target).path for target in targets]


def require_html(response: ResponseLike, status_code: int, label: str) -> None:
    require(
        response.status_code == status_code,
        f"{label}: expected {status_code}, got {response.status_code}",
    )
    content_type = response.headers.get("content-type", "")
    require(content_type.startswith("text/html"), f"{label}: response is not HTML")


def require_safe_rendered_urls(response: ResponseLike, label: str) -> None:
    for invalid_text in ("/books/books", "{book_id}", 'action=""'):
        require(
            invalid_text not in response.body,
            f"{label}: found invalid rendered text {invalid_text}",
        )


def require_paths(
    response: ResponseLike,
    *,
    links: set[str] | None = None,
    actions: set[str] | None = None,
    label: str,
) -> None:
    targets = html_targets(response.body)
    rendered_links = set(target_paths(targets.links))
    rendered_actions = set(target_paths(targets.form_actions))
    for expected_link in links or set():
        require(expected_link in rendered_links, f"{label}: link missing {expected_link}")
    for expected_action in actions or set():
        require(
            expected_action in rendered_actions,
            f"{label}: form action missing {expected_action}",
        )
    require("" not in targets.form_actions, f"{label}: empty form action rendered")
    require_safe_rendered_urls(response, label)


def field_fragment(body: str, field_name: str) -> str:
    field_position = body.find(f'name="{field_name}"')
    require(field_position >= 0, f"form field not rendered: {field_name}")
    next_field_position = body.find('<div class="field">', field_position)
    if next_field_position < 0:
        return body[field_position:]
    return body[field_position:next_field_position]


def require_field_error(
    response: ResponseLike,
    field_name: str,
    expected_message: str,
    label: str,
) -> None:
    require_html(response, 200, label)
    fragment = field_fragment(response.body, field_name)
    require(
        expected_message in fragment,
        f"{label}: expected {field_name} error containing {expected_message!r}",
    )


def require_original_input(
    response: ResponseLike,
    field_name: str,
    value: str,
    label: str,
) -> None:
    fragment = field_fragment(response.body, field_name)
    expected_value = f'value="{escape(value, quote=True)}"'
    require(
        expected_value in fragment,
        f"{label}: original {field_name} value was not preserved",
    )
