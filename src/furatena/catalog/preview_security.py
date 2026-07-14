"""Fail-closed identity and access controls for pull-request previews."""

from __future__ import annotations

import base64
import binascii
import dataclasses
import os
import re
import secrets
from collections.abc import Mapping
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from chirp.http.request import Request
from chirp.http.response import Response, SSEResponse
from chirp.middleware.protocol import AnyResponse, Next

_SHA_RE = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
_TRUE_VALUES = frozenset({"1", "true", "yes"})
_PROBE_PATHS = frozenset({"/healthz", "/readyz"})
_ROBOTS_POLICY = "noindex, nofollow, noarchive, nosnippet"
_PRIVATE_CACHE = "private, no-store"
_AUTH_REALM = 'Basic realm="Furatena pull-request preview", charset="UTF-8"'


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


def _secure_response(response: AnyResponse, *, protected: bool) -> AnyResponse:
    if isinstance(response, SSEResponse):
        return response
    response = _replace_header(response, "X-Robots-Tag", _ROBOTS_POLICY)
    if not protected:
        return response
    response = _replace_header(response, "Cache-Control", _PRIVATE_CACHE)
    existing_vary = _header_value(response, "Vary") or ""
    vary = {item.strip() for item in existing_vary.split(",") if item.strip()}
    vary.add("Authorization")
    return _replace_header(response, "Vary", ", ".join(sorted(vary)))


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
