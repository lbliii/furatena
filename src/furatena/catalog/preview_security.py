"""Fail-closed identity and access controls for pull-request previews."""

from __future__ import annotations

import asyncio
import base64
import binascii
import dataclasses
import html
import json
import os
import re
import secrets
from collections.abc import Mapping
from dataclasses import dataclass, field
from http.cookies import CookieError, SimpleCookie
from urllib.parse import urlsplit

from chirp.http.request import Request
from chirp.http.response import Response, SSEResponse
from chirp.middleware.protocol import AnyResponse, Next

from furatena.catalog.preview_grant_runtime import (
    PREVIEW_CALLBACK_PATH,
    PreviewGrantRuntime,
    PreviewGrantRuntimeError,
)

_SHA_RE = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
_TRUE_VALUES = frozenset({"1", "true", "yes"})
_PROBE_PATHS = frozenset({"/healthz", "/readyz"})
_ROBOTS_POLICY = "noindex, nofollow, noarchive, nosnippet"
_PRIVATE_CACHE = "private, no-store"
_AUTH_REALM = 'Basic realm="Furatena pull-request preview", charset="UTF-8"'
_GRANT_AUTH_REALM = 'Bearer realm="Furatena hosted preview", error="invalid_token"'
_MACHINE_SUFFIXES = (
    ".css",
    ".gif",
    ".ico",
    ".jpeg",
    ".jpg",
    ".js",
    ".json",
    ".md",
    ".png",
    ".svg",
    ".txt",
    ".webmanifest",
    ".xml",
)
_MACHINE_PREFIXES = (
    "/_fura/",
    "/api/",
    "/catalog",
    "/cli",
    "/dcp/",
    "/graph/",
    "/llms",
    "/mcp/",
    "/metadata",
    "/query",
)


class PreviewConfigurationError(ValueError):
    """Raised when explicit preview-environment identity is incomplete or unsafe."""


def _enabled(value: str | None) -> bool:
    return (value or "").strip().lower() in _TRUE_VALUES


def _required(environ: Mapping[str, str], name: str) -> str:
    value = environ.get(name, "").strip()
    if not value:
        raise PreviewConfigurationError(f"{name} is required for a pull-request preview")
    return value


def _https_url(value: str, name: str, *, origin_only: bool = False) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or (origin_only and (parsed.path not in {"", "/"} or parsed.query))
    ):
        qualifier = " HTTPS origin" if origin_only else "n HTTPS URL"
        raise PreviewConfigurationError(f"{name} must be a{qualifier} without credentials")
    return f"https://{parsed.netloc}" if origin_only else value


def _preview_origin(environ: Mapping[str, str]) -> str:
    configured = environ.get("FURA_PREVIEW_ORIGIN", "").strip().rstrip("/")
    if configured:
        return _https_url(configured, "FURA_PREVIEW_ORIGIN", origin_only=True)
    railway_domain = environ.get("RAILWAY_PUBLIC_DOMAIN", "").strip().rstrip("/")
    if not railway_domain:
        raise PreviewConfigurationError(
            "FURA_PREVIEW_ORIGIN or RAILWAY_PUBLIC_DOMAIN is required for a pull-request preview"
        )
    origin = railway_domain if "://" in railway_domain else f"https://{railway_domain}"
    return _https_url(origin, "RAILWAY_PUBLIC_DOMAIN", origin_only=True)


@dataclass(frozen=True, slots=True)
class PreviewEnvironment:
    """Public, non-secret identity for one commit-bound review environment."""

    pull_request_number: int
    head_sha: str
    review_url: str
    origin: str

    @property
    def short_sha(self) -> str:
        return self.head_sha[:12]

    @property
    def template_context(self) -> dict[str, str | int]:
        return {
            "pull_request_number": self.pull_request_number,
            "head_sha": self.head_sha,
            "short_sha": self.short_sha,
            "review_url": self.review_url,
            "origin": self.origin,
        }

    @classmethod
    def from_environment(
        cls,
        environ: Mapping[str, str] | None = None,
    ) -> PreviewEnvironment | None:
        values = (
            {
                "FURA_PR_PREVIEW": os.environ.get("FURA_PR_PREVIEW", ""),
                "FURA_PREVIEW_PR_NUMBER": os.environ.get("FURA_PREVIEW_PR_NUMBER", ""),
                "FURA_PREVIEW_SHA": os.environ.get("FURA_PREVIEW_SHA", ""),
                "FURA_BUILD_GIT_SHA": os.environ.get("FURA_BUILD_GIT_SHA", ""),
                "FURA_PREVIEW_REVIEW_URL": os.environ.get("FURA_PREVIEW_REVIEW_URL", ""),
                "FURA_PREVIEW_ORIGIN": os.environ.get("FURA_PREVIEW_ORIGIN", ""),
                "RAILWAY_PUBLIC_DOMAIN": os.environ.get("RAILWAY_PUBLIC_DOMAIN", ""),
            }
            if environ is None
            else environ
        )
        if not _enabled(values.get("FURA_PR_PREVIEW")):
            return None

        raw_number = _required(values, "FURA_PREVIEW_PR_NUMBER")
        try:
            pull_request_number = int(raw_number)
        except ValueError as error:
            raise PreviewConfigurationError(
                "FURA_PREVIEW_PR_NUMBER must be a positive integer"
            ) from error
        if pull_request_number < 1:
            raise PreviewConfigurationError("FURA_PREVIEW_PR_NUMBER must be a positive integer")

        head_sha = _required(values, "FURA_PREVIEW_SHA").lower()
        if _SHA_RE.fullmatch(head_sha) is None:
            raise PreviewConfigurationError(
                "FURA_PREVIEW_SHA must be a 40- or 64-character lowercase hexadecimal SHA"
            )
        build_sha = values.get("FURA_BUILD_GIT_SHA", "").strip().lower()
        if build_sha and build_sha != head_sha:
            raise PreviewConfigurationError(
                "FURA_PREVIEW_SHA must match the immutable FURA_BUILD_GIT_SHA"
            )

        review_url = _https_url(
            _required(values, "FURA_PREVIEW_REVIEW_URL"),
            "FURA_PREVIEW_REVIEW_URL",
        )
        return cls(
            pull_request_number=pull_request_number,
            head_sha=head_sha,
            review_url=review_url,
            origin=_preview_origin(values),
        )


@dataclass(frozen=True, slots=True)
class PreviewAccessCredentials:
    """Deployment-only preview credential, excluded from representations."""

    token: str = field(repr=False)

    @classmethod
    def from_environment(
        cls,
        environ: Mapping[str, str] | None = None,
    ) -> PreviewAccessCredentials:
        values = (
            {"FURA_PREVIEW_AUTH_TOKEN": os.environ.get("FURA_PREVIEW_AUTH_TOKEN", "")}
            if environ is None
            else environ
        )
        token = _required(values, "FURA_PREVIEW_AUTH_TOKEN")
        if len(token) < 32:
            raise PreviewConfigurationError(
                "FURA_PREVIEW_AUTH_TOKEN must contain at least 32 characters"
            )
        return cls(token=token)


def _replace_header(response: AnyResponse, name: str, value: str) -> AnyResponse:
    if isinstance(response, SSEResponse):
        # Frozen preview mode registers no SSE routes. Keep the type total for
        # Chirp's response union without invoking SSEResponse's no-op headers.
        return response
    filtered = tuple(
        (header, existing)
        for header, existing in response.headers
        if header.lower() != name.lower()
    )
    return dataclasses.replace(response, headers=(*filtered, (name, value)))


def _header_value(response: AnyResponse, name: str) -> str | None:
    if isinstance(response, SSEResponse):
        return None
    return next(
        (value for header, value in response.headers if header.lower() == name.lower()),
        None,
    )


def _secure_response(
    response: AnyResponse,
    *,
    protected: bool,
    vary_cookie: bool = False,
) -> AnyResponse:
    if isinstance(response, SSEResponse):
        return response
    response = _replace_header(response, "X-Robots-Tag", _ROBOTS_POLICY)
    if not protected:
        return response
    response = _replace_header(response, "Cache-Control", _PRIVATE_CACHE)
    existing_vary = _header_value(response, "Vary") or ""
    vary = {item.strip() for item in existing_vary.split(",") if item.strip()}
    vary.add("Authorization")
    if vary_cookie:
        vary.add("Cookie")
    return _replace_header(response, "Vary", ", ".join(sorted(vary)))


def _cookie_value(request: Request, name: str) -> str | None:
    raw = request.headers.get("cookie")
    if not raw or len(raw) > 8192:
        return None
    cookies = SimpleCookie()
    try:
        cookies.load(raw)
    except CookieError:
        return None
    morsel = cookies.get(name)
    return morsel.value if morsel is not None else None


def _bearer_grant(request: Request) -> str | None:
    authorization = request.headers.get("authorization")
    if not authorization:
        return None
    scheme, separator, credential = authorization.partition(" ")
    if not separator or scheme.lower() != "bearer":
        return None
    value = credential.strip()
    return value if value else None


def _browser_navigation(request: Request) -> bool:
    if request.method not in {"GET", "HEAD"} or request.headers.get("authorization"):
        return False
    path = request.path.lower()
    if path.startswith(_MACHINE_PREFIXES) or path.endswith(_MACHINE_SUFFIXES):
        return False
    accept = (request.headers.get("accept") or "").lower()
    if not accept:
        return True
    return "text/html" in accept or "application/xhtml+xml" in accept


def _grant_denial(error: PreviewGrantRuntimeError | None = None) -> Response:
    retryable = error is not None and error.retryable
    status = 503 if retryable or (error is not None and error.status >= 500) else 401
    payload = {
        "error": error.code if error is not None else "authentication_required",
        "message": (
            str(error) if error is not None else "A valid hosted preview Bearer grant is required."
        ),
        "remediation": (
            "Retry authorization later."
            if status == 503
            else "Obtain a current grant for this exact preview revision and retry."
        ),
        "retryable": status == 503,
    }
    return Response(
        json.dumps(payload, sort_keys=True, separators=(",", ":")),
        status=status,
        content_type="application/problem+json; charset=utf-8",
    ).with_header("WWW-Authenticate", _GRANT_AUTH_REALM)


def _browser_failure(error: PreviewGrantRuntimeError) -> Response:
    status = 503 if error.retryable or error.status >= 500 else 401
    title = "Preview sign-in unavailable" if status == 503 else "Preview sign-in failed"
    body = (
        '<!doctype html><html><head><meta name="robots" '
        'content="noindex,nofollow,noarchive,nosnippet"><title>'
        f"{title}</title></head><body><main><h1>{title}</h1>"
        f"<p>{html.escape(str(error))}</p>"
        "<p>Return to the preview and restart authorization.</p>"
        "</main></body></html>"
    )
    return Response(body, status=status, content_type="text/html; charset=utf-8")


def _session_cookie(name: str, token: str, max_age: int) -> str:
    return f"{name}={token}; Path=/; Max-Age={max_age}; Secure; HttpOnly; SameSite=Lax"


def _expired_session_cookie(name: str) -> str:
    return f"{name}=; Path=/; Max-Age=0; Secure; HttpOnly; SameSite=Lax"


def _basic_token(value: str) -> str | None:
    encoded = value.removeprefix("Basic ").strip()
    try:
        decoded = base64.b64decode(encoded, validate=True).decode("utf-8")
    except binascii.Error, UnicodeDecodeError:
        return None
    username, separator, password = decoded.partition(":")
    if not separator or username != "preview":
        return None
    return password


def _provided_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    if authorization.startswith("Bearer "):
        return authorization.removeprefix("Bearer ").strip()
    if authorization.startswith("Basic "):
        return _basic_token(authorization)
    return None


class PreviewSecurityMiddleware:
    """Authenticate review surfaces and apply preview anti-indexing policy."""

    __slots__ = ("_credentials",)

    def __init__(self, credentials: PreviewAccessCredentials) -> None:
        self._credentials = credentials

    async def __call__(self, request: Request, next: Next) -> AnyResponse:
        if request.path in _PROBE_PATHS:
            return _secure_response(await next(request), protected=False)

        provided = _provided_token(request.headers.get("authorization"))
        if provided is None or not secrets.compare_digest(provided, self._credentials.token):
            response = Response(
                "Pull-request preview authentication required.\n",
                status=401,
                content_type="text/plain; charset=utf-8",
            ).with_header("WWW-Authenticate", _AUTH_REALM)
            return _secure_response(response, protected=True)

        return _secure_response(await next(request), protected=True)


class PreviewGrantSecurityMiddleware:
    """Authenticate hosted previews with local grants and SHA-bound sessions."""

    __slots__ = ("_runtime",)

    def __init__(self, runtime: PreviewGrantRuntime) -> None:
        self._runtime = runtime

    async def __call__(self, request: Request, next: Next) -> AnyResponse:
        if request.path in _PROBE_PATHS:
            return _secure_response(await next(request), protected=False)

        if request.path == PREVIEW_CALLBACK_PATH:
            return await self._callback(request)

        bearer = _bearer_grant(request)
        if bearer is not None:
            try:
                await asyncio.to_thread(self._runtime.authenticate_bearer, bearer)
            except PreviewGrantRuntimeError as error:
                self._runtime.record_denial()
                return _secure_response(
                    _grant_denial(error),
                    protected=True,
                    vary_cookie=True,
                )
            return _secure_response(await next(request), protected=True, vary_cookie=True)

        cookie_name = self._runtime.config.cookie_name
        session_token = _cookie_value(request, cookie_name)
        if (
            session_token is not None
            and self._runtime.authenticate_session(session_token) is not None
        ):
            return _secure_response(await next(request), protected=True, vary_cookie=True)

        self._runtime.record_denial()
        if not _browser_navigation(request):
            return _secure_response(_grant_denial(), protected=True, vary_cookie=True)

        try:
            login = await asyncio.to_thread(self._runtime.start_browser_login, request.path)
        except PreviewGrantRuntimeError as error:
            return _secure_response(_browser_failure(error), protected=True, vary_cookie=True)
        response = Response("", status=302).with_header("Location", login.location)
        if session_token is not None:
            response = response.with_header("Set-Cookie", _expired_session_cookie(cookie_name))
        return _secure_response(response, protected=True, vary_cookie=True)

    async def _callback(self, request: Request) -> AnyResponse:
        if request.method != "GET":
            error = PreviewGrantRuntimeError(
                "The preview authorization callback requires GET.",
                code="invalid_callback",
            )
            return _secure_response(_browser_failure(error), protected=True, vary_cookie=True)
        code = request.query.get("code")
        state = request.query.get("state")
        if set(request.query) != {"code", "state"} or not code or not state:
            error = PreviewGrantRuntimeError(
                "The preview authorization callback is incomplete or denied.",
                code="invalid_callback",
            )
            return _secure_response(_browser_failure(error), protected=True, vary_cookie=True)
        try:
            established = await asyncio.to_thread(
                self._runtime.complete_browser_login,
                code=code,
                state=state,
            )
        except PreviewGrantRuntimeError as error:
            self._runtime.record_denial()
            return _secure_response(_browser_failure(error), protected=True, vary_cookie=True)
        cookie = _session_cookie(
            self._runtime.config.cookie_name,
            established.token,
            established.max_age_seconds,
        )
        return _secure_response(
            Response("", status=303)
            .with_header("Location", established.return_path)
            .with_header("Set-Cookie", cookie),
            protected=True,
            vary_cookie=True,
        )
