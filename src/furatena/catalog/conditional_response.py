"""Conditional request validators for dynamic documentation responses."""

from __future__ import annotations

import dataclasses
import hashlib
from collections.abc import Callable
from datetime import UTC, datetime
from email.utils import format_datetime, parsedate_to_datetime

from chirp.http.request import Request
from chirp.http.response import Response
from chirp.middleware.protocol import Next

LastModifiedResolver = Callable[[Request], float | None]


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

        last_modified_epoch = self._last_modified(request)
        if last_modified_epoch is not None:
            last_modified = format_datetime(
                datetime.fromtimestamp(last_modified_epoch, tz=UTC).replace(microsecond=0),
                usegmt=True,
            )
            response = response.with_header("Last-Modified", last_modified)

        etag = _response_etag(response)
        if etag is not None:
            response = response.with_header("ETag", etag)

        if etag is not None and _etag_matches(request.headers.get("if-none-match"), etag):
            return dataclasses.replace(response, body="", status=304)
        if (
            not request.headers.get("if-none-match")
            and last_modified_epoch is not None
            and _not_modified_since(request.headers.get("if-modified-since"), last_modified_epoch)
        ):
            return dataclasses.replace(response, body="", status=304)
        return response


def _response_etag(response: Response) -> str | None:
    content_type = response.content_type.partition(";")[0].strip().lower()
    if content_type != "text/markdown" and not (
        content_type == "application/json" or content_type.endswith("+json")
    ):
        return None
    body = response.body.encode("utf-8") if isinstance(response.body, str) else response.body
    digest = hashlib.sha256(body).hexdigest()
    return f'"{digest}"'


def _etag_matches(value: str | None, etag: str) -> bool:
    if not value:
        return False
    return any(candidate.strip().removeprefix("W/") in {"*", etag} for candidate in value.split(","))


def _not_modified_since(value: str | None, last_modified_epoch: float) -> bool:
    if not value:
        return False
    try:
        requested = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        return False
    if requested.tzinfo is None:
        requested = requested.replace(tzinfo=UTC)
    return int(last_modified_epoch) <= int(requested.timestamp())
