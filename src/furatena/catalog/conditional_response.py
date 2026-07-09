"""Conditional request validators for dynamic documentation responses."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import UTC, datetime
from email.utils import format_datetime

from chirp.http.request import Request
from chirp.http.response import Response
from chirp.middleware.protocol import Next
from chirp.server.conditional import evaluate_conditional_response

LastModifiedResolver = Callable[[Request], float | None]
_PRELOAD_CACHE_CONTROL = "private, max-age=60"


class ConditionalResponseMiddleware:
    """Attach validators and answer conditional GETs for dynamic responses."""

    __slots__ = ("_last_modified",)

    def __init__(self, last_modified: LastModifiedResolver) -> None:
        self._last_modified = last_modified

    async def __call__(self, request: Request, next: Next):
        response = await next(request)
        if request.method not in {"GET", "HEAD"} or not isinstance(response, Response):
            return response
        if response.status != 200:
            return response

        if (
            request.headers.get("HX-Preloaded") == "true"
            and response.content_type.lower().startswith("text/html")
            and response.header("Cache-Control") is None
        ):
            # The preload extension relies on the browser HTTP cache. Keep the
            # response private because page visibility can depend on the user.
            response = response.with_header("Cache-Control", _PRELOAD_CACHE_CONTROL)

        etag = response.header("ETag") or _response_etag(response)
        if etag is None:
            # HTML contains a per-request CSP nonce, so its rendered bytes are
            # not stable and must never advertise a reusable validator.
            return response

        if response.header("ETag") is None:
            response = response.with_header("ETag", etag)
        last_modified_epoch = self._last_modified(request)
        if response.header("Last-Modified") is None and last_modified_epoch is not None:
            last_modified = format_datetime(
                datetime.fromtimestamp(last_modified_epoch, tz=UTC).replace(microsecond=0),
                usegmt=True,
            )
            response = response.with_header("Last-Modified", last_modified)

        return evaluate_conditional_response(request, response)


def _response_etag(response: Response) -> str | None:
    content_type = response.content_type.partition(";")[0].strip().lower()
    if content_type != "text/markdown" and not (
        content_type == "application/json" or content_type.endswith("+json")
    ):
        return None
    body = response.body.encode("utf-8") if isinstance(response.body, str) else response.body
    digest = hashlib.sha256(body).hexdigest()
    return f'"{digest}"'
