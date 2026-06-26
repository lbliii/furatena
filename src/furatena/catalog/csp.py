"""Content-Security-Policy helpers for the docs shell."""

from __future__ import annotations

import dataclasses
import re

from chirp.http.request import Request
from chirp.http.response import FileResponse, Response, StreamingResponse
from chirp.middleware.protocol import AnyResponse, Next

_GOOGLE_FONTS_STYLE = "https://fonts.googleapis.com"
_GOOGLE_FONTS_FONT = "https://fonts.gstatic.com"

_STYLE_SRC_RE = re.compile(r"(style-src\s[^;]*)")


def extend_csp_for_google_fonts(csp: str) -> str:
    """Allow the docs shell to load Noto Sans Linear B from Google Fonts."""
    if not csp.strip() or _GOOGLE_FONTS_STYLE in csp:
        return csp

    updated = csp
    match = _STYLE_SRC_RE.search(updated)
    if match is not None:
        style_src = match.group(1)
        if _GOOGLE_FONTS_STYLE not in style_src:
            updated = updated.replace(style_src, f"{style_src} {_GOOGLE_FONTS_STYLE}", 1)
    else:
        updated = f"{updated}; style-src 'self' 'unsafe-inline' {_GOOGLE_FONTS_STYLE}"

    if "font-src " not in updated:
        updated = f"{updated}; font-src 'self' {_GOOGLE_FONTS_FONT} data:"

    if "connect-src " not in updated:
        updated = f"{updated}; connect-src 'self' {_GOOGLE_FONTS_STYLE} {_GOOGLE_FONTS_FONT}"

    return updated


def _csp_value(response: AnyResponse) -> str | None:
    for header, value in response.headers:
        if header.lower() == "content-security-policy":
            return value
    return None


def _replace_csp_header(response: AnyResponse, value: str) -> AnyResponse:
    filtered = tuple(
        (header, val)
        for header, val in response.headers
        if header.lower() != "content-security-policy"
    )
    return dataclasses.replace(response, headers=(*filtered, ("Content-Security-Policy", value)))


class GoogleFontsCSPMiddleware:
    """Extend chirp-ui's nonce CSP so external Google Fonts assets are permitted."""

    __slots__ = ()

    async def __call__(self, request: Request, next: Next) -> AnyResponse:
        response = await next(request)
        if not isinstance(response, (Response, StreamingResponse, FileResponse)):
            return response

        current = _csp_value(response)
        if current is None:
            return response

        extended = extend_csp_for_google_fonts(current)
        if extended == current:
            return response
        return _replace_csp_header(response, extended)
