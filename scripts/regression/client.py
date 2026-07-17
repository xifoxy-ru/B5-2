from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

from scripts.regression.assertions import require


@dataclass(frozen=True)
class ASGIResponse:
    status_code: int
    headers: dict[str, str]
    body: str


class DirectASGIClient:
    def __init__(self, app: Any) -> None:
        self.app = app

    async def request(
        self,
        method: str,
        path: str,
        *,
        query: str = "",
        form: dict[str, str] | None = None,
    ) -> ASGIResponse:
        body = urlencode(form).encode() if form is not None else b""
        headers = [(b"host", b"testserver")]
        if form is not None:
            headers.extend(
                [
                    (b"content-type", b"application/x-www-form-urlencoded"),
                    (b"content-length", str(len(body)).encode()),
                ]
            )

        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "scheme": "http",
            "method": method,
            "path": path,
            "raw_path": path.encode(),
            "query_string": query.encode(),
            "root_path": "",
            "headers": headers,
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 80),
        }
        messages: list[dict[str, Any]] = []
        request_sent = False

        async def receive() -> dict[str, Any]:
            nonlocal request_sent
            if not request_sent:
                request_sent = True
                return {"type": "http.request", "body": body, "more_body": False}
            return {"type": "http.disconnect"}

        async def send(message: dict[str, Any]) -> None:
            messages.append(message)

        await self.app(scope, receive, send)

        start_message = next(
            (
                message
                for message in messages
                if message["type"] == "http.response.start"
            ),
            None,
        )
        require(start_message is not None, f"{method} {path} did not start a response")
        response_body = b"".join(
            message.get("body", b"")
            for message in messages
            if message["type"] == "http.response.body"
        )
        response_headers = {
            key.decode().lower(): value.decode()
            for key, value in start_message["headers"]
        }
        return ASGIResponse(
            status_code=start_message["status"],
            headers=response_headers,
            body=response_body.decode(),
        )
